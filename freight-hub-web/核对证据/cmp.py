import json,collections
F=json.load(open('fh_results.json'))
def fh_rows(case,pid):
    return [(r['channel'],round(float(r['total']),2),(r.get('currency') or 'CNY')) for r in F[case]['rows'] if r['platform']==pid]
def compare(fh,portal):
    a=collections.Counter(fh); b=collections.Counter(portal)
    only_fh=a-b; only_p=b-a
    return {'fh':len(fh),'portal':len(portal),'match':sum((a&b).values()),'only_fh':sorted(only_fh.elements()),'only_portal':sorted(only_p.elements())}
if __name__=='__main__':
    import urllib.parse
    P=json.load(open('zh_portal.json'))
    for k,v in P.items():
        lst=json.loads(v['body'])['body']['list']
        pr=[(it['name'] or it['quoteName'],round(float(it['standardFeeSum']),2),it.get('standardCur') or 'CNY') for it in lst]
        c=compare(fh_rows(k,'zhonghang'),pr)
        print(k,c['fh'],c['portal'],c['match'],c['only_fh'][:5],c['only_portal'][:5])
