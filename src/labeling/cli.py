from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from .config import LabelingConfigError, load_labeling_config
from .model import OpenAILabelModel
from .runner import (
    ARTICLES_ROOT,
    LabelingError,
    discover_article_dirs,
    discover_run_article_dirs,
    resolve_article_dir,
    resolve_run_dir,
    run_labeling,
)
from .schema import load_tree_spec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Label crawled WeChat articles through one API model")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--article-dir", action="append", default=[], help="label only this article directory; repeatable")
    scope.add_argument("--run-dir", help="label exactly the articles recorded by one results/record run")
    parser.add_argument("--limit", type=int, help="limit pending articles for a test run")
    parser.add_argument("--concurrency", type=int, help="override labeling.concurrency for this run")
    parser.add_argument("--replace", action="store_true", help="relabel articles that already have a valid v2 label")
    parser.add_argument("--check", action="store_true", help="validate configuration and rules without calling the model")
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = build_parser()
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.concurrency is not None and args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    try:
        config = load_labeling_config()
        tree = load_tree_spec()
        scoped_dirs = None
        resolved_run = None
        if args.run_dir:
            resolved_run = resolve_run_dir(args.run_dir)
            scoped_dirs = discover_run_article_dirs(resolved_run)

        if args.check:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "provider": config.provider,
                        "model": config.model,
                        "base_url": config.base_url,
                        "api_style": config.api_style,
                        "config_source": config.source,
                        "api_key_source": config.api_key_source,
                        "concurrency": args.concurrency or config.concurrency,
                        "timeout_seconds": config.timeout_seconds,
                        "max_retries": config.max_retries,
                        "tree_version": tree["version"],
                        "decision_nodes": len(tree["nodes"]),
                        "article_root": str(ARTICLES_ROOT),
                        "run_dir": str(resolved_run) if resolved_run else None,
                        "run_article_count": len(scoped_dirs) if scoped_dirs is not None else None,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        if scoped_dirs is not None:
            article_dirs = scoped_dirs
        elif args.article_dir:
            article_dirs = [resolve_article_dir(raw) for raw in args.article_dir]
        else:
            article_dirs = discover_article_dirs()
        if args.limit is not None:
            article_dirs = article_dirs[: args.limit]
        result = asyncio.run(
            run_labeling(
                article_dirs,
                OpenAILabelModel(config),
                concurrency=args.concurrency or config.concurrency,
                max_retries=config.max_retries,
                replace=args.replace,
            )
        )
        result["model"] = config.model
        result["provider"] = config.provider
        result["api_style"] = config.api_style
        result["config_source"] = config.source
        result["run_dir"] = str(resolved_run) if resolved_run else None
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["failed"] else 0
    except (LabelingConfigError, LabelingError, OSError, UnicodeDecodeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
