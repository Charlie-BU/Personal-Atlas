"""Generate a public demo using fictional data only; never connects to a service."""
import argparse
import base64
from pathlib import Path
import sys
from types import SimpleNamespace
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import atlas
from visualization import collect, export


def build(output):
    files = {}
    commit = None

    def snapshot():
        return atlas.Snapshot(dict(files), commit)

    def accept(prepared):
        # Synthetic commit receiver, not a real remote publisher.
        nonlocal commit
        assert prepared['status'] == 'prepared' and prepared['base_commit'] == commit
        for path in prepared['delete_paths']:
            files.pop(path)
        files.update({p: base64.b64decode(v) for p, v in prepared['files_base64'].items()})
        commit = atlas.digest(atlas.encoded({p: atlas.digest(v) for p, v in sorted(files.items())}))[:40]

    accept(atlas.initialize(snapshot()))
    # Every entry below is fictional. No personal vault, configuration, or network is read.
    entries = [
        ('lin','person','林溪 · 虚构人物','education','一个喜欢设计、技术与自然的虚构人物。',None,[]),
        ('lab','organization','拾光实验室','work','虚构的跨学科设计团队。',None,[]),
        ('city','place','云湾','travel','示例地图里的虚构城市。',None,[]),
        ('garden','project','城市花园计划','technology','用开放工具记录社区花园的生长与协作。','2026-03',[('about','lin'),('related_to','lab')]),
        ('journal','routine','每周观察笔记','life','每周末整理观察，把原话与感受分开记录。',None,[('related_to','garden')]),
        ('walk','preference','步行探索街区','travel','慢下来观察街区，沿路记录新的发现。',None,[('about','lin')]),
        ('learn','goal','学习生态设计','education','通过阅读、实验和协作建立自己的知识地图。','2026-09',[('about','lin'),('related_to','garden')]),
        ('launch','event','第一次社区分享','work','展示花园原型，收集邻居对下一阶段的建议。','2026-05-16',[('part_of','garden'),('related_to','lab')]),
        ('move','event','开始在云湾生活','life','一个新的生活阶段，也是一段观察练习的开始。','2025-08',[('about','lin'),('located_at','city')]),
        ('craft','topic','设计与自然','culture','用小作品探索人与环境之间的关系。',None,[('related_to','garden')]),
        ('tea','preference','一杯茶的留白','food','下午留一点时间喝茶与阅读。',None,[('about','lin')]),
        ('share','decision','把方法写成开放笔记','values','让可复用的方法有来源，也允许后来修正。','2026-06',[('related_to','garden')]),
        ('read','routine','晨间阅读','education','每天阅读几页，并记录可追溯的想法。',None,[('related_to','learn')]),
        ('trail','goal','完成一条自然观察路线','travel','将路线、照片描述与时间整理成一个小作品。','2026-10',[('located_at','city'),('related_to','craft')]),
        ('camera','asset','随身观察相机','devices','只记录示例设备用途，不包含序列号或账户。',None,[('related_to','trail')]),
        ('mentor','person','周遥 · 虚构同伴','work','一起讨论花园计划的虚构协作者。',None,[('knows','lin'),('related_to','lab')]),
    ]
    entries += [
        ('identity','profile','把观察变成作品的人','identity','林溪的虚构自述：希望把技术、设计与日常观察连接起来。',None,[('about','lin'),('related_to','craft')]),
        ('sister','person','林岚 · 虚构家人','family','定期一起做饭、分享阅读，也是生活记录中的重要家人。',None,[('knows','lin')]),
        ('family_day','routine','每月一次家庭共餐','family','用一起准备饭菜的时间交换近况，尊重不愿记录的内容。',None,[('about','sister'),('related_to','tea')]),
        ('college','organization','青禾设计学社','education','虚构学习社群，组织开放课程和设计练习。',None,[('related_to','lab')]),
        ('course','event','完成服务设计课程','education','通过社区访谈和原型练习学习服务设计，产出一份观察报告。','2024-06',[('about','lin'),('related_to','college'),('related_to','learn')]),
        ('notebook','asset','田野观察手册','devices','随身记录天气、植物和对话要点，整理后保留来源。',None,[('related_to','journal'),('related_to','trail')]),
        ('sensor','project','花园微气候记录器','technology','制作低功耗温湿度原型，用来比较花园不同角落的环境。','2026-07',[('part_of','garden'),('related_to','lab'),('related_to','camera')]),
        ('library','place','云湾公共书屋','culture','虚构公共阅读空间，周末举办小型作品交流。',None,[('located_at','city'),('related_to','college')]),
        ('bookclub','routine','周末共读一小时','culture','和同伴讨论一本书的不同理解，不把意见误记成事实。',None,[('located_at','library'),('about','mentor'),('related_to','read')]),
        ('first_notes','event','第一册田野笔记完成','culture','整理十二次步行观察，第一次把散落的记忆连成主题。','2025-11-08',[('related_to','journal'),('related_to','notebook')]),
        ('running','routine','周三轻松慢跑','sport','以舒适节奏完成日常运动，具体强度随状态调整。',None,[('about','lin'),('related_to','walk')]),
        ('greenway','place','南岸绿道','sport','虚构的沿河步行与慢跑路线，也是自然观察地点。',None,[('located_at','city'),('related_to','trail')]),
        ('first_run','event','完成春季五公里练习','sport','和同伴完成一次非竞速练习，记录感受而非成绩排名。','2026-04-12',[('related_to','running'),('located_at','greenway')]),
        ('sleep','goal','建立稳定的晚间节奏','health','睡前留出安静时间，让第二天的学习与活动更从容。','2026-08',[('about','lin'),('related_to','read')]),
        ('quiet','preference','先安静整理再讨论','psychology','复杂问题先独立写下想法，再和同伴交换观点。',None,[('about','lin'),('related_to','journal')]),
        ('reflection','routine','月末回顾与留白','psychology','每月检查目标变化和记录冲突，给未知保留位置。',None,[('related_to','learn'),('related_to','sleep'),('related_to','share')]),
        ('budget','routine','按月整理项目支出','finance','仅保存虚构预算习惯，不包含真实余额或支付账户。',None,[('related_to','garden'),('related_to','sensor')]),
        ('fund','decision','先做小规模原型','finance','控制初期投入，等社区反馈明确后再扩展材料采购。','2026-02',[('related_to','garden'),('related_to','budget')]),
        ('language','goal','用英文介绍一个完整项目','language','准备三分钟项目介绍，能够说明背景、证据与下一步。','2026-11',[('about','lin'),('related_to','garden'),('related_to','learn')]),
        ('speaking','routine','每周双语表达练习','language','用自己的话解释观察笔记，再请同伴指出歧义。',None,[('related_to','language'),('about','mentor')]),
        ('feedback','preference','具体、温和、可行动的反馈','communication','喜欢带示例的反馈；对不确定的建议明确标注假设。',None,[('about','lin'),('related_to','quiet')]),
        ('meeting','routine','协作前写清问题与边界','communication','讨论前列出目的、已知信息和需要决定的事项。',None,[('related_to','lab'),('related_to','feedback')]),
        ('workshop','event','主持第一次观察工作坊','communication','邀请虚构居民用地图分享日常路线，记录经授权的观点。','2026-08-22',[('part_of','garden'),('located_at','library'),('related_to','meeting')]),
        ('care','topic','可追溯，也允许改变','values','记录来源是为了理解，不用旧记录限制一个人的变化。',None,[('related_to','share'),('about','lin')]),
        ('privacy','decision','公开方法，私藏个人资料','values','公开示例全部使用虚构素材，真实资料和导出文件单独保管。','2026-06-18',[('related_to','share'),('related_to','care')]),
        ('seasonal','preference','按季节安排简单饭菜','food','尝试少量新食材，并保留适合家人共同准备的菜谱。',None,[('related_to','family_day'),('related_to','garden')]),
        ('recipe','project','一季一页的厨房笔记','food','把季节食材、共同烹饪和家庭故事整理成小册子。','2026-09',[('about','sister'),('related_to','seasonal'),('related_to','notebook')]),
        ('island','place','雾屿自然步道','travel','另一处虚构观察目的地，有海风、岩石与迁徙鸟类的主题。',None,[('related_to','city')]),
        ('trip','event','秋日步道考察','travel','带着相机与手册探索新路线，比较沿途的植被与公共设施。','2026-10-03',[('located_at','island'),('related_to','trail'),('related_to','camera')]),
        ('exhibit','goal','完成年度观察小展','culture','用地图、照片描述和短文呈现一年的生活观察。','2026-12',[('related_to','trail'),('related_to','garden'),('located_at','library')]),
        ('role','state','从独立制作到共同协作','work','虚构阶段记录：把更多精力投入社区沟通、共同验证与方法整理。','2026-05',[('about','lin'),('related_to','lab'),('related_to','meeting')]),
        ('proposal','event','花园提案收到第一轮反馈','work','同伴建议减少功能、先解决浇水与观察记录两个具体问题。','2026-03-28',[('part_of','garden'),('about','mentor'),('related_to','fund')]),
        ('revision','decision','把展示改为参与式地图','technology','根据反馈，先让参与者补充观察，再设计自动化功能。','2026-04-06',[('related_to','proposal'),('related_to','garden'),('related_to','care')]),
        ('archive','project','社区观察开放手册','technology','将经授权的方法、流程与虚构示例整理为可复用的公开资料。','2026-10',[('related_to','privacy'),('related_to','workshop'),('related_to','share')]),
        ('neighbors','organization','南岸观察小组','life','虚构的社区协作小组，成员可以选择匿名参与。',None,[('located_at','city'),('related_to','garden'),('related_to','privacy')]),
        ('weekend','routine','周末留出一段无计划时间','life','在目标之外保留散步、休息和偶遇，不要求每段时间都有产出。',None,[('related_to','walk'),('related_to','sleep'),('related_to','care')]),
        ('plan','goal','把年度计划拆成季度实验','identity','每季度选择一个可验证的小问题，同时允许调整方向。','2026-01',[('about','lin'),('related_to','reflection'),('related_to','learn')]),
        ('checkin','event','第三季度回顾','life','回看课程、协作和生活节奏，决定放慢同时推进项目的数量。','2026-09-30',[('related_to','reflection'),('related_to','plan'),('related_to','weekend')]),
        ('device_setup','decision','用简单工具保持可迁移性','devices','笔记采用通用文件格式，设备变化时不丢失来源与上下文。','2026-07-09',[('related_to','notebook'),('related_to','archive')]),
        ('language_day','event','双语项目介绍练习完成','language','用三分钟说明花园计划，再根据同伴提问补充证据和范围。','2026-10-12',[('related_to','language'),('related_to','speaking'),('about','mentor')]),
    ]
    raw='\n'.join(title+'：'+summary for _,_,title,_,summary,_,_ in entries).encode()
    imported=atlas.ingest(snapshot(),SimpleNamespace(name='fictional-demo.txt',title='虚构演示资料 / Fictional demo',summary='全部内容均为虚构，仅用于公开界面演示。',domain=['life']),raw)
    accept(imported)
    changes=[]
    for i,(rid,kind,title,domain,summary,when,relations) in enumerate(entries,1):
        meta={'id':rid,'type':kind,'title':title,'summary':summary,'domains':[domain],
              'basis':'document','temporal':{'mode':'point','at':when} if when else {'mode':'none'},
              'relations':[{'type':t,'target':r} for t,r in relations],
              'evidence':[{'source':imported['source_id'],'locator':'lines:'+str(i)}]}
        changes.append({'action':'put','meta':meta,'body':summary+'\n\n虚构演示资料，不代表真实个人。'})
    accept(atlas.apply_plan(snapshot(),{'operation_id':'op_public_demo','base_commit':commit,'changes':changes}))
    data=collect(snapshot())
    # Public fixture does not link to a real personal-data repository.
    for node in data['nodes']:
        node.pop('url',None)
    return export(data,output)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,help='New HTML file to create (must not exist)')
    print(build(parser.parse_args().output))
