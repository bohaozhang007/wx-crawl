from pathlib import Path
source=Path('/root/workspace/wx-crawl/slides/report/build.py').read_text()
exec(source.split('txt(.65,.38')[0])
O=Path('/root/workspace/wx-crawl/slides/flowchart');O.mkdir(exist_ok=True)
def link(points,head=True):
 for i,((x,y),(xx,yy)) in enumerate(zip(points,points[1:])):
  sh=s.shapes.add_connector(1,Inches(x),Inches(y),Inches(xx),Inches(yy));sh.line.color.rgb=RGBColor.from_string(BLUE);sh.line.width=Pt(1.15)
  d.line((x*scale,y*scale,xx*scale,yy*scale),fill='#'+BLUE,width=2)
  if head and i==len(points)-2:
   end=OxmlElement('a:tailEnd');end.set('type','triangle');sh._element.spPr.get_or_add_ln().append(end)
   import math
   angle=math.atan2(yy-y,xx-x);tip=(xx*scale,yy*scale);u=(math.cos(angle),math.sin(angle));v=(-u[1],u[0]);d.polygon([tip,(tip[0]-8*u[0]+4*v[0],tip[1]-8*u[1]+4*v[1]),(tip[0]-8*u[0]-4*v[0],tip[1]-8*u[1]-4*v[1])],fill='#'+BLUE)
def node(x,y,w,h,title,tool):
 box(x,y,w,h);txt(x+.14,y+.18,w-.28,.35,title,18,INK,True);txt(x+.14,y+.66,w-.28,h-.67,tool,12,BLUE)
txt(.65,.38,12,.25,'WX-CRAWL  /  PIPELINE',10,MUT)
txt(.65,.97,12,.64,'微信公众号科研机会 · 自动化处理流程',29,INK,True)
txt(.68,1.78,12,.38,'从信息采集到机会提醒，以大模型识别内容，用明确规则控制筛选。',16,MUT)
node(.65,2.63,1.65,1.43,'定时触发','Hermes')
node(2.67,2.63,1.8,1.43,'列表采集','TikHub\n增量获取、去重')
node(4.84,2.63,2.27,1.43,'正文下载','wechat-mp-tools\nwe-mp-rss')
node(7.49,2.63,1.94,1.43,'智能打标','DeepSeek\n类型、方向、时效')
for a,b in [(2.3,2.67),(4.47,4.84),(7.11,7.49),(9.43,9.88)]:link([(a,3.34),(b,3.34)])
# The rules decision is a real editable flowchart diamond.
x,y,w,h=9.89,2.48,2.82,1.76
sh=s.shapes.add_shape(MSO_SHAPE.DIAMOND,Inches(x),Inches(y),Inches(w),Inches(h));sh.fill.solid();sh.fill.fore_color.rgb=RGBColor.from_string(PALE);sh.line.color.rgb=RGBColor.from_string(LINE)
d.polygon([((x+w/2)*scale,y*scale),((x+w)*scale,(y+h/2)*scale),((x+w/2)*scale,(y+h)*scale),(x*scale,(y+h/2)*scale)],fill='#'+PALE,outline='#'+LINE)
txt(10.56,2.98,1.5,.37,'条件符合？',17,INK,True)
txt(10.54,3.48,1.5,.3,'Python 规则筛选',10,BLUE)
# Only failed local downloads take the paid fallback branch.
box(4.84,4.62,2.27,.73,PALE,PALE);txt(5,4.84,1.95,.3,'TikHub 正文兜底',13,BLUE)
link([(5.97,4.06),(5.97,4.62)]);txt(6.08,4.17,1.3,.26,'下载失败',10,MUT)
link([(7.11,4.99),(7.28,4.99),(7.28,3.34),(7.49,3.34)])
# Selected opportunities and non-selected / review cases diverge.
link([(11.3,4.24),(11.3,4.76)]);txt(11.44,4.36,.9,.23,'符合',10,BLUE)
box(9.84,4.78,2.92,1.0,PALE,PALE);txt(10,4.97,2.59,.3,'SQLite → 钉钉 AI 表',15,INK,True);txt(10,5.44,2.6,.25,'重要机会提醒 / 截止提醒',11,BLUE)
link([(12.71,3.36),(13.02,3.36),(13.02,6.02),(8.5,6.02),(8.5,5.58)])
box(7.57,4.78,1.87,.8);txt(7.74,4.92,1.56,.46,'排除 / 人工复核',12,INK,True)
txt(9.86,6.09,2.9,.22,'不符合或无法确定',10,MUT)
box(.65,6.42,12.1,.6,PALE,PALE)
txt(.84,6.59,11.72,.3,'筛选：科研项目 / 指南申报  ·  方向匹配  ·  全国性国家级、北京、浙江  ·  未过期',14,INK)
txt(.72,7.18,12,.2,'截止时间不明可继续筛选；地域不明转人工复核。正文优先开源获取，失败再调用 TikHub。',10,MUT)
R.core_properties.title='微信公众号科研机会处理流程｜一页流程图'
p=O/'科研机会Pipeline-简化流程图.pptx';R.save(p);im.save(O/'preview.png')
check=Presentation(p);assert len(check.slides)==1
for sh in check.slides[0].shapes:assert sh.left>=0 and sh.top>=0 and sh.left+sh.width<=R.slide_width and sh.top+sh.height<=R.slide_height
print(p)
