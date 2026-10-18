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
