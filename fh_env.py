import re,sys,json,time,requests
PW=re.search(r"密码[:：]\s*(\S+)",open('/workspace/freight-hub-web/ADMIN_PASSWORD.txt',encoding='utf-8').read()).group(1)
B="https://freight.gaosichun.dev"
s=requests.Session();H={"Origin":B,"Content-Type":"application/json"}
r=s.post(B+"/api/auth/login",headers=H,json={"username":"admin","password":PW},timeout=60);H["X-CSRF-Token"]=r.json()["csrf"]
def J(m,u,**kw):
    for i in range(3):
        try:
            x=s.request(m,B+u,headers=H,timeout=200,**kw)
            if x.status_code<500: return x.json()
        except Exception as e: err=e
        time.sleep(3)
    return {"error":"retry-failed"}
def plat(pid):
    p=[x for x in J("GET","/api/platforms") if x["id"]==pid][0]
    return {"env":p["env"],"tag":p["env_tag"],"state":p["state"],"label":p["label"],"envs":{e["value"]:e["configured"] for e in p["envs"]}}
def quote(pid,c="NZ",w=0.3,pc="0932"):
    j=J("POST","/api/quote",json={"country":c,"weight":w,"postcode":pc,"platforms":[pid]})
    if "id" in j: J("DELETE",f"/api/history/{j['id']}")
    pl=[x for x in j.get("platforms",[]) if x["id"]==pid]
    rows=[r for r in j.get("rows",[])]
    return {"plat":{k:pl[0].get(k) for k in ("state","count","category","env","env_tag","message")} if pl else None,
            "rows":[(r["channel"][:16],r["total"],r.get("env"),bool(r.get("excluded")),r["remark"][:30]) for r in rows[:3]]}
cmd=sys.argv[1]
if cmd=="yanwen":
    print("start",plat("yanwen"))
    print("test q",quote("yanwen"))
    print("->prod",J("PUT","/api/platforms/yanwen/account",json={"values":{},"env":"prod"}).get("env"),plat("yanwen"))
    print("prod q",quote("yanwen"))
    print("->test",J("PUT","/api/platforms/yanwen/account",json={"values":{},"env":"sandbox"}).get("env"),plat("yanwen"))
    print("test q again",quote("yanwen"))
if cmd=="sf":
    print("before",plat("shunfeng"))
    x=J("PUT","/api/platforms/shunfeng/account",json={"values":{"customer_code":"ICRM-CNE0103T471"},"env":"prod","auth_mode":"api"})
    print("saved",x.get("env"),plat("shunfeng"))
    t=J("POST","/api/platforms/shunfeng/test",json={})
    print("test",json.dumps(t,ensure_ascii=False)[:600])
    print("quote US 1kg",json.dumps(quote("shunfeng","US",1,""),ensure_ascii=False)[:900])
if cmd=="sfp":
    codes=sys.argv[2]
    print(J("PUT","/api/platforms/shunfeng/account",json={"values":{"products":codes},"env":"prod"}).get("env"))
    print(json.dumps(quote("shunfeng",sys.argv[3] if len(sys.argv)>3 else "US",1,sys.argv[4] if len(sys.argv)>4 else "10001"),ensure_ascii=False)[:1200])
