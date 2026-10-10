"""Enrich existing database articles, without relabeling decisions or sending alerts."""
import argparse
import asyncio
import json
from pathlib import Path
import sqlite3

from .config import load_labeling_config
from .model import OpenAIProjectIntakeModel
from .project_intake import INSTRUCTIONS, normalize, table_fields
from src.storage.db import DEFAULT_DB_PATH, query_articles, init_database


async def enrich(rows, concurrency=2):
    model = OpenAIProjectIntakeModel(load_labeling_config())
    sem = asyncio.Semaphore(concurrency)
    async def one(row):
        async with sem:
            try:
                raw, usage = await model.label(INSTRUCTIONS, json.dumps({'title': row['title'], 'content':row.get('content_text','')},ensure_ascii=False))
                cleaned = normalize(raw, row.get('content_text',''))
                return {'id':row['id'], 'status':'ok', 'project_intake':cleaned,
                        'fields':table_fields(cleaned,row.get('content_text','')), 'usage':usage}
            except Exception as exc:
                return {'id':row['id'], 'status':'failed', 'error_type':type(exc).__name__}
    try:
        return await asyncio.gather(*(one(row) for row in rows))
    finally:
        await model.client.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db',type=Path,default=DEFAULT_DB_PATH)
    p.add_argument('--article-id',type=int,action='append')
    p.add_argument('--limit',type=int)
    p.add_argument('--apply',action='store_true',help='persist verified fields; default saves a preview only')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    rows=query_articles(args.db,limit=1000000)
    if args.article_id: rows=[r for r in rows if r['id'] in args.article_id]
    if args.limit is not None: rows=rows[:args.limit]
    results=asyncio.run(enrich(rows))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(results,ensure_ascii=False,indent=2))
    if args.apply:
        backup=args.output.with_suffix('.sqlite3.bak')
        with sqlite3.connect(args.db) as source, sqlite3.connect(backup) as dest: source.backup(dest)
        init_database(args.db)
        with sqlite3.connect(args.db) as connection:
            for item in results:
                if item['status']=='ok' and item['fields']:
                    # Re-read both evidence and prior enrichment before committing.
                    row=connection.execute('SELECT content_text,project_intake_json FROM articles WHERE id=?',(item['id'],)).fetchone()
                    if row:
                        clean=normalize(item['project_intake'],row[0]);prior=normalize(json.loads(row[1]),row[0])
                        merged={**prior,**clean}
                        connection.execute('UPDATE articles SET project_intake_json=? WHERE id=?',(json.dumps(merged,ensure_ascii=False),item['id']))
    print(json.dumps({'status':'partial' if any(r['status']=='failed' for r in results) else 'ok',
                      'articles':len(results),'with_fields':sum(bool(r.get('fields')) for r in results),
                      'applied':args.apply,'details_file':str(args.output.resolve())},ensure_ascii=False,separators=(',',':')))
    return int(any(r['status']=='failed' for r in results))

if __name__=='__main__':raise SystemExit(main())
