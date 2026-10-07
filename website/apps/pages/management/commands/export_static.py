"""Export and validate the finite public website artifact."""

from __future__ import annotations

import re
import shutil
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test import Client, override_settings

from apps.blog import posts as blog
from apps.docs import nav


class _References(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values: list[str] = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in {"href", "src"} and value:
                self.values.append(value)


class Command(BaseCommand):
    help = "Render and validate the static website"

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True, type=Path)
        parser.add_argument("--base-url", default=None)
        parser.add_argument("--demo-artifact", required=True, type=Path)

    def handle(self, *args, **options):
        output = options["output"].resolve()
        demo_artifact = options["demo_artifact"].resolve()
        base_url = self._base_url(options["base_url"] or settings.SITE_BASE_URL)
        source_root = Path(settings.BASE_DIR).resolve()
        if self._protected_output(output, source_root):
            raise CommandError(f"output is inside or above a protected source path: {output}")
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            raise CommandError(f"output must be a new or empty directory: {output}")
        if (
            output == demo_artifact
            or output.is_relative_to(demo_artifact)
            or demo_artifact.is_relative_to(output)
        ):
            raise CommandError("output and demo gallery artifact must be separate")

        versions = self._validate_sources()
        routes = self._routes(versions)
        relative_paths = [relative for _, relative in routes]
        duplicate = next(
            (path for path in relative_paths if relative_paths.count(path) > 1), None
        )
        if duplicate:
            raise CommandError(f"duplicate output path: {duplicate}")

        output.mkdir(parents=True, exist_ok=True)
        origin = urlsplit(base_url)
        client = Client()
        with override_settings(
            SITE_BASE_URL=base_url,
            ANALYSIS_DEMO_AVAILABLE=(demo_artifact / "checkout-findings" / "index.html").is_file(),
        ):
            for route, relative in routes:
                if route == "/docs/":
                    body = self._docs_redirect(versions[0])
                elif route == "/404.html":
                    response = client.get("/404.html", HTTP_HOST=origin.netloc)
                    if response.status_code != 404:
                        raise CommandError("404 handler did not return HTTP 404")
                    body = response.content
                else:
                    response = client.get(
                        route,
                        HTTP_HOST=origin.netloc,
                        secure=origin.scheme == "https",
                    )
                    if response.status_code != 200:
                        raise CommandError(f"{route} rendered HTTP {response.status_code}")
                    body = response.content
                if not body:
                    raise CommandError(f"{route} rendered an empty response")
                self._write(output / relative, body)

        shutil.copytree(source_root / "static", output / "static")
        demo_files = self._copy_demo(demo_artifact, output / "demo")
        (output / ".nojekyll").write_text("", encoding="utf-8")
        (output / "CNAME").write_text(origin.hostname + "\n", encoding="utf-8")
        self._validate_artifact(output, set(relative_paths), origin.netloc, demo_files)
        self.stdout.write(self.style.SUCCESS(f"Exported {len(routes)} routes to {output}"))

    @staticmethod
    def _copy_demo(source: Path, destination: Path) -> set[str]:
        if not source.is_dir():
            raise CommandError(f"demo gallery artifact is missing: {source}")
        if source.is_symlink() or any(path.is_symlink() for path in source.rglob("*")):
            raise CommandError("demo gallery artifact must not contain symlinks")
        if (
            not (source / "index.html").is_file()
            or not (source / "_vendor").is_dir()
            or not (source / "_vendor" / "chart.umd.min.js").is_file()
        ):
            raise CommandError(
                "demo gallery artifact requires index.html and the _vendor/ runtime"
            )
        raw = next(
            (
                path
                for path in source.rglob("*")
                if path.is_file()
                and path.suffix.lower() in {".csv", ".sqlite", ".sqlite3", ".db", ".duckdb"}
            ),
            None,
        )
        if raw:
            raise CommandError(
                f"raw warehouse file in demo gallery artifact: {raw.relative_to(source)}"
            )

        shutil.copytree(source, destination)
        for obsolete in ("CNAME", "robots.txt"):
            (destination / obsolete).unlink(missing_ok=True)
        return {
            (Path("demo") / path.relative_to(destination)).as_posix()
            for path in destination.rglob("*")
            if path.is_file()
        }

    @staticmethod
    def _base_url(value: str) -> str:
        parsed = urlsplit(value)
        try:
            parsed.port
        except ValueError as error:
            raise CommandError(f"invalid base URL: {error}") from error
        if (
            any(character.isspace() or ord(character) < 32 for character in value)
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or not re.fullmatch(r"[A-Za-z0-9.-]+", parsed.hostname)
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise CommandError("base URL must be an http(s) origin without credentials, path, query, or fragment")
        return value.rstrip("/")

    @staticmethod
    def _protected_output(output: Path, source_root: Path) -> bool:
        protected_subtrees = [
            Path(settings.DOCS_ROOT).resolve(),
            (source_root / "templates").resolve(),
            (source_root / "static").resolve(),
        ]
        return (
            output == source_root
            or source_root.is_relative_to(output)
            or any(
                output == path or output.is_relative_to(path)
                for path in protected_subtrees
            )
        )

    @staticmethod
    def _validate_sources() -> list[str]:
        root = Path(settings.DOCS_ROOT)
        if not root.is_dir():
            raise CommandError(f"documentation corpus is missing: {root}")
        versions = nav.versions()
        if not versions:
            raise CommandError("documentation corpus has no published versions")
        for version in versions:
            nav_path = root / version / "nav.yml"
            if not nav_path.is_file():
                raise CommandError(f"documentation navigation is missing: {nav_path}")
            for page in nav.pages(version):
                if nav.source_path(version, page.slug) is None:
                    raise CommandError(f"navigation references missing page: {page.url}")
        for post in blog.posts():
            if blog.source_path(post.slug) is None:
                raise CommandError(f"blog index references missing post: {post.slug}")
        return versions

    @staticmethod
    def _routes(versions: list[str]) -> list[tuple[str, str]]:
        routes = [
            ("/", "index.html"),
            ("/privacy/", "privacy/index.html"),
            ("/terms/", "terms/index.html"),
            ("/cookies/", "cookies/index.html"),
            ("/tour/", "tour/index.html"),
            ("/blog/", "blog/index.html"),
            ("/blog/rss.xml", "blog/rss.xml"),
            ("/robots.txt", "robots.txt"),
            ("/sitemap.xml", "sitemap.xml"),
        ]
        routes.extend((post.url, f"blog/{post.slug}/index.html") for post in blog.posts())
        for version in versions:
            routes.extend(
                [
                    (f"/docs/{version}/", f"docs/{version}/index.html"),
                    (f"/docs/{version}/search.json", f"docs/{version}/search.json"),
                ]
            )
            routes.extend(
                (page.url, f"docs/{version}/{page.slug}/index.html")
                for page in nav.pages(version)
            )
        return routes + [("/docs/", "docs/index.html"), ("/404.html", "404.html")]

    @staticmethod
    def _write(path: Path, body: bytes):
        # Documentation can legitimately teach template syntax in code examples.
        # Check rendered HTML outside code blocks, where an unresolved token would
        # be visible page content or markup.
        checked = re.sub(rb"<(?:pre|code)\b[^>]*>.*?</(?:pre|code)>", b"", body, flags=re.I | re.S)
        if path.suffix == ".html" and re.search(
            rb"\{\{[^}]+\}\}|\{%[^%]+%\}", checked
        ):
            raise CommandError(f"unresolved template placeholder in {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    @classmethod
    def _validate_artifact(
        cls, output: Path, routes: set[str], host: str, imported: set[str] | None = None
    ):
        static = {
            (Path("static") / path.relative_to(settings.BASE_DIR / "static")).as_posix()
            for path in (settings.BASE_DIR / "static").rglob("*")
            if path.is_file()
        }
        expected = routes | static | {".nojekyll", "CNAME"} | (imported or set())
        actual = {
            path.relative_to(output).as_posix()
            for path in output.rglob("*")
            if path.is_file()
        }
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise CommandError(f"artifact file set mismatch; missing={missing}, extra={extra}")

        for source in output.rglob("*"):
            if source.suffix == ".html":
                parser = _References()
                parser.feed(source.read_text(encoding="utf-8"))
                refs = parser.values
            elif source.suffix == ".css":
                refs = re.findall(r"url\(\s*['\"]?([^'\")]+)", source.read_text(encoding="utf-8"))
            else:
                continue
            for ref in refs:
                target = cls._local_target(output, source, ref, host)
                if target is not None and not target.is_file():
                    raise CommandError(
                        f"broken local reference in {source.relative_to(output)}: {ref}"
                    )

    @staticmethod
    def _local_target(output: Path, source: Path, value: str, host: str) -> Path | None:
        parsed = urlsplit(value)
        if parsed.scheme in {"mailto", "tel", "data", "javascript"} or value.startswith("//"):
            return None
        if parsed.scheme and (parsed.scheme not in {"http", "https"} or parsed.netloc != host):
            return None
        if not parsed.path:
            return None
        path = unquote(parsed.path)
        if path.startswith("/"):
            target = output / path.lstrip("/")
        else:
            target = source.parent / path
        if path.endswith("/"):
            target /= "index.html"
        elif not target.suffix:
            target /= "index.html"
        target = target.resolve()
        if not target.is_relative_to(output.resolve()):
            raise CommandError(f"local reference escapes the artifact: {value}")
        return target

    @staticmethod
    def _docs_redirect(version: str) -> bytes:
        target = f"/docs/{version}/"
        return (
            '<!doctype html><meta charset="utf-8"><title>Documentation</title>'
            f'<meta http-equiv="refresh" content="0; url={target}">'
            f'<p>Documentation moved to <a href="{target}">{target}</a>.</p>'
        ).encode()
