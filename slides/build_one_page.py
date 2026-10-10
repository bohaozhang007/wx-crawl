from pathlib import Path
source=Path('/root/workspace/wx-crawl/slides/build.py').read_text()
exec(source.split("s=slide(1,")[0])
OUT=Path('/root/workspace/wx-crawl/slides/one-page');OUT.mkdir(exist_ok=True)
s=r.slides.add_slide(r.slide_layouts[6]);s.background.fill.solid();s.background.fill.fore_color.rgb=rgb(BG);slides.append([])
text(s,.7,.4,11,.3,'WX-CRAWL  /  WORKFLOW',11,MUTED)
text(s,.7,1.05,12,.7,'微信公众号 · 爬取与筛选流程',32,INK,True)
text(s,.74,1.93,11.8,.45,'只保留符合范围的科研机会，自动入库并提醒。',17,MUTED)
items=[('01','获取列表','TikHub V2\n增量拉取 · URL 去重'),('02','下载正文','wechat-mp-tools\n↓ 失败后尝试\nwe-mp-rss → TikHub 兜底'),('03','打标筛选','国家级 / 北京 / 浙江\n排除已截止及无关文章\n地域不明 → 人工复核'),('04','入库与提醒','SQLite → 钉钉 AI 表\n高重要度提醒\n申报截止提醒')]
for i,(num,title,body) in enumerate(items):
 x=.73+3.07*i
 box(s,x,2.92,2.65,2.55)
 text(s,x+.2,3.13,2.25,.28,num,11,ACC)
 text(s,x+.2,3.67,2.25,.42,title,22,INK,True)
 text(s,x+.2,4.33,2.25,1.05,body,13,MUTED)
 if i<3:line(s,x+2.69,4.2,x+3.01,4.2)
text(s,.77,6.01,11.85,.43,'范围看项目归属：其他省份专项不因“国家级主办”放行。截止时间不明可继续筛选。',14,ACC)
line(s,.73,6.95,12.6,6.95,False)
text(s,.75,7.08,11,.2,'开源正文优先  /  失败再兜底  /  发送前复核',10,MUTED)
r.core_properties.title='微信公众号爬取与筛选流程｜一页版'
path=OUT/'微信公众号爬取与筛选流程-一页版.pptx';r.save(path)
assert len(Presentation(path).slides)==1
exec(source[source.index("fontpath=Path("):source.index(" contact=Image.new")])
print(path)
