"""El repositorio de la web: leer los registros, escribir artículos y subir ramas.

El agente trabaja en su propio clon (VCA_STATE_DIR/site), nunca en
la carpeta de la web en producción. Lee los registros con PHP por
reflexión, sin arrancar Laravel, así que solo necesita el binario de PHP.
"""

from __future__ import annotations

import base64
import html
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .render import php_str
from .util import child_env

DUMP_PHP = r"""
$root = $argv[1];
foreach (['Blog', 'Services', 'Portfolio'] as $class) {
    require $root.'/app/Support/'.$class.'.php';
}
$call = function ($class, $method) {
    return (new ReflectionMethod($class, $method))->invoke(null);
};
echo json_encode([
    'posts' => $call(App\Support\Blog::class, 'posts'),
    'categories' => App\Support\Blog::CATEGORIES,
    'services' => $call(App\Support\Services::class, 'services'),
    'projects' => $call(App\Support\Portfolio::class, 'projects'),
], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
"""

STATIC_PATHS = {"/", "/servicios", "/blog", "/portfolio", "/identidad", "/ahora"}
REGISTRY_KEYS = ("slug", "title", "excerpt", "category", "date", "modified", "reading")


class SiteError(Exception):
    pass


@dataclass
class SiteData:
    posts: List[Dict[str, Any]]
    categories: List[str]
    services: List[Dict[str, Any]]
    projects: List[Dict[str, Any]]

    def post(self, slug: str) -> Optional[Dict[str, Any]]:
        return next((p for p in self.posts if p["slug"] == slug), None)

    def service(self, slug: str) -> Optional[Dict[str, Any]]:
        return next((s for s in self.services if s["slug"] == slug), None)

    @property
    def post_slugs(self) -> Set[str]:
        return {p["slug"] for p in self.posts}

    @property
    def paths(self) -> Set[str]:
        paths = set(STATIC_PATHS)
        paths.update(f"/blog/{p['slug']}" for p in self.posts)
        paths.update(f"/servicios/{s['slug']}" for s in self.services)
        paths.update(f"/portfolio/{p['slug']}" for p in self.projects)
        return paths


# --- Edición del registro Blog.php (funciones puras, con tests) ---------------


def registry_line(post: Dict[str, Any]) -> str:
    parts = [f"{php_str(k)} => {php_str(str(post[k]))}" for k in REGISTRY_KEYS if post.get(k)]
    return " " * 12 + "[" + ", ".join(parts) + "],"


def insert_post(source: str, line: str) -> str:
    """Añade una entrada al principio de Blog::posts() (el primero es el destacado)."""
    match = re.search(r"private static function posts\(\)[^{]*\{\s*\n(\s*)return \[\n", source)
    if not match:
        raise SiteError("No encuentro 'return [' de Blog::posts()")
    return source[: match.end()] + line + "\n" + source[match.end():]


def replace_post(source: str, slug: str, line: str) -> str:
    pattern = re.compile(r"^[ \t]*\['slug' => " + re.escape(php_str(slug)) + r",.*\],[ \t]*$", re.M)
    matches = pattern.findall(source)
    if len(matches) != 1:
        raise SiteError(f"Esperaba una entrada con el slug {slug} en Blog.php y hay {len(matches)}")
    return pattern.sub(lambda _: line, source, count=1)


def extract_sections(view: str) -> Dict[str, str]:
    """Texto de las secciones intro y body de una vista de artículo, sin etiquetas."""
    out: Dict[str, str] = {}
    for name in ("intro", "body"):
        match = re.search(r"@section\('" + name + r"'\)(.*?)@endsection", view, re.S)
        if match:
            text = re.sub(r"\{\{\s*url\('([^']+)'\)\s*\}\}", r"\1", match.group(1))
            text = re.sub(r"<(h2|h3)>", r"\n## ", text)
            text = re.sub(r"<li>", "\n- ", text)
            text = re.sub(r"<[^>]+>", "", text)
            text = html.unescape(re.sub(r"@(verbatim|endverbatim)", "", text))
            out[name] = re.sub(r"\n{3,}", "\n\n", text).strip()
    return out


def write_lf(path: Path, text: str) -> None:
    """Escribe con finales LF. `Path.write_text(newline=…)` no existe hasta Python 3.10."""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


class SiteRepo:
    def __init__(self, directory: Path, repo: str, token: str, base: str, git_bin: str, php_bin: str,
                 author_name: str, author_email: str):
        self.dir = Path(directory)
        self.repo = repo
        self.token = token
        self.base = base
        self.git_bin = git_bin
        self.php_bin = php_bin
        self.author_name = author_name
        self.author_email = author_email

    def _env(self) -> Dict[str, str]:
        env = child_env()
        env.update(
            GIT_TERMINAL_PROMPT="0",
            GIT_AUTHOR_NAME=self.author_name,
            GIT_AUTHOR_EMAIL=self.author_email,
            GIT_COMMITTER_NAME=self.author_name,
            GIT_COMMITTER_EMAIL=self.author_email,
        )
        if self.token:
            basic = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
            env.update(
                GIT_CONFIG_COUNT="1",
                GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {basic}",
            )
        return env

    def git(self, *args: str, cwd: Optional[Path] = None) -> str:
        proc = subprocess.run(
            [self.git_bin, *args], cwd=str(cwd or self.dir), env=self._env(),
            capture_output=True, text=True, encoding="utf-8", timeout=300,
        )
        if proc.returncode != 0:
            raise SiteError(f"git {args[0]} ha fallado: {proc.stderr.strip()[-500:]}")
        return proc.stdout.strip()

    def sync(self) -> None:
        """Clona si hace falta y deja el clon limpio en origin/<base>."""
        if not (self.dir / ".git").exists():
            self.dir.parent.mkdir(parents=True, exist_ok=True)
            self.git("clone", "--quiet", f"https://github.com/{self.repo}.git", str(self.dir), cwd=self.dir.parent)
        self.git("fetch", "--quiet", "--prune", "origin")
        self.git("checkout", "--quiet", "--force", "-B", self.base, f"origin/{self.base}")
        self.git("clean", "--quiet", "-fd")

    def load(self) -> SiteData:
        proc = subprocess.run(
            [self.php_bin, "-r", DUMP_PHP, "--", str(self.dir)],
            capture_output=True, text=True, encoding="utf-8", timeout=60, env=child_env(),
        )
        if proc.returncode != 0:
            raise SiteError(f"PHP no ha podido leer los registros: {proc.stderr.strip()[-500:]}")
        data = json.loads(proc.stdout)
        return SiteData(
            posts=data["posts"], categories=data["categories"],
            services=data["services"], projects=data["projects"],
        )

    def view_path(self, slug: str) -> Path:
        return self.dir / "resources" / "views" / "blog" / f"{slug}.blade.php"

    def read_article(self, slug: str) -> Dict[str, str]:
        return extract_sections(self.view_path(slug).read_text(encoding="utf-8"))

    def write_article(self, slug: str, view: str, registry_entry: Dict[str, Any], kind: str) -> List[str]:
        blog_php = self.dir / "app" / "Support" / "Blog.php"
        source = blog_php.read_text(encoding="utf-8")
        line = registry_line(registry_entry)
        source = insert_post(source, line) if kind == "new" else replace_post(source, slug, line)
        write_lf(blog_php, source)
        write_lf(self.view_path(slug), view)
        return ["app/Support/Blog.php", f"resources/views/blog/{slug}.blade.php"]

    def lint(self, paths: List[str]) -> None:
        for path in paths:
            if path.endswith(".php") and not path.endswith(".blade.php"):
                proc = subprocess.run([self.php_bin, "-l", str(self.dir / path)], capture_output=True, text=True, timeout=60)
                if proc.returncode != 0:
                    raise SiteError(f"Error de sintaxis PHP en {path}: {proc.stdout.strip()[-300:]}")

    def commit_branch(self, branch: str, paths: List[str], message: str, push: bool = True) -> str:
        """Crea la rama desde origin/<base> con los cambios ya escritos y la sube. Devuelve el SHA."""
        self.git("checkout", "--quiet", "-B", branch)
        self.git("add", "--", *paths)
        self.git("commit", "--quiet", "-m", message)
        sha = self.git("rev-parse", "HEAD")
        if push:
            self.git("push", "--quiet", "--force", "origin", f"{branch}:{branch}")
        self.git("checkout", "--quiet", "--force", self.base)
        return sha
