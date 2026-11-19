from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
F="Microsoft YaHei"
wb=Workbook(); ws=wb.active; ws.title="报价单"
widths={"A":6,"B":24,"C":60,"D":11,"E":12,"F":22}
for k,v in widths.items(): ws.column_dimensions[k].width=v
thin=Side(style="thin",color="808080"); B=Border(left=thin,right=thin,top=thin,bottom=thin)
HDR=PatternFill("solid",fgColor="1F4E78"); SEC=PatternFill("solid",fgColor="D9E1F2"); TOT=PatternFill("solid",fgColor="FFF2CC"); OPT=PatternFill("solid",fgColor="E2EFDA")
def f(sz=10,b=False,c="000000"): return Font(name=F,size=sz,bold=b,color=c)
wrap=Alignment(wrap_text=True,vertical="center"); ctr=Alignment(horizontal="center",vertical="center",wrap_text=True)
rgt=Alignment(horizontal="right",vertical="center")
MONEY='"¥"#,##0.00'
r=1
ws.merge_cells("A1:F1"); ws["A1"]="软件开发报价单"; ws["A1"].font=f(20,True,"1F4E78"); ws["A1"].alignment=ctr; ws.row_dimensions[1].height=36
ws.merge_cells("A2:F2"); ws["A2"]="项目名称：FreightHub 货代统一询价系统"; ws["A2"].font=f(12,True); ws["A2"].alignment=ctr; ws.row_dimensions[2].height=22
r=4
info=[("报价编号","FH-Q-20261031-01","报价日期","2026-10-31"),
      ("供方（乙方）","","客户（甲方）",""),
      ("联系人","","联系人",""),
      ("联系电话","","联系电话",""),
      ("币种","人民币（CNY）","报价有效期","30 天（至 2026-11-30）")]
for a,b,c,d in info:
    ws.cell(r,1,a); ws.merge_cells(start_row=r,start_column=1,end_row=r,end_column=2)
    ws.cell(r,3,b); ws.cell(r,4,c); ws.merge_cells(start_row=r,start_column=4,end_row=r,end_column=5); ws.cell(r,6,d)
    for col in range(1,7):
        x=ws.cell(r,col); x.border=B; x.alignment=wrap; x.font=f(10)
    for col in (1,4): ws.cell(r,col).font=f(10,True); ws.cell(r,col).fill=SEC
    ws.row_dimensions[r].height=20; r+=1
r+=1
def section(title):
    global r
    ws.merge_cells(start_row=r,start_column=1,end_row=r,end_column=6)
    c=ws.cell(r,1,title); c.font=f(12,True,"1F4E78"); c.alignment=Alignment(vertical="center"); ws.row_dimensions[r].height=22; r+=1
def header():
    global r
    for i,h in enumerate(["序号","模块","工作内容","工作量(人天)","单价(元/人天)","小计(元)"],1):
        c=ws.cell(r,i,h); c.font=f(10,True,"FFFFFF"); c.fill=HDR; c.alignment=ctr; c.border=B
    ws.row_dimensions[r].height=24; r+=1
section("一、开发明细")
ws.merge_cells(start_row=r,start_column=1,end_row=r,end_column=4)
ws.cell(r,1,"人天单价（参考国内外包行情，可调整）：").font=f(10,True); ws.cell(r,1).alignment=rgt
ws.cell(r,5,1200).number_format=MONEY; ws.cell(r,5).font=f(10,True,"C00000"); ws.cell(r,5).fill=TOT; ws.cell(r,5).border=B
pass
RATE=f"$E${r}"; r+=1
header()
items=[
("需求调研与接口分析","业务需求调研；7 个货代平台（云途/中航/欣航/环洋/燕文/顺丰/雨桐）登录方式、询价接口、报文结构抓包分析；输出需求规格说明与接口分析文档",5),
("系统架构与后端框架","总体架构设计；FastAPI 后端服务；统一询价数据模型；多平台异步并发查询与超时控制；会话保持与失效自动重登；异常重试、降级与容错；日志与配置管理",8),
("平台适配器－云途","登录（含图形验证码识别/处理）、会话维护、询价接口对接、结果标准化",5),
("平台适配器－中航","请求签名算法逆向分析与实现、登录鉴权、询价接口对接、结果标准化",7),
("平台适配器－欣航","登录与页面抓取，HTML 解析提取报价数据，结果标准化",3),
("平台适配器－环洋","复用 V5 版本既有对接逻辑，适配统一接口并回归验证",2),
("平台适配器－燕文","基于官方开放 API 对接（鉴权、询价），结果标准化",2),
("平台适配器－顺丰","登录鉴权、询价接口对接、结果标准化",3),
("桌面客户端 UI","主窗口框架：标题栏、菜单栏、工具栏、树形导航、多标签页、状态栏；询价数据网格：多列排序、条件筛选、右键菜单、导出 CSV；询价表单与结果对比展示",10),
("平台账号管理与查询历史","各平台账号新增/编辑/删除/启停、凭据本地加密存储、登录状态检测；查询历史记录保存、检索与复查",4),
("Windows 打包与安装","Windows 安装包制作（无控制台窗口运行）、WebView2 运行时检测与引导安装、快捷方式/卸载程序、版本号管理",3),
("端到端测试","编写并执行 50 项端到端测试用例（覆盖各平台询价、重登、容错、UI 操作、导出、安装）；缺陷修复与回归；测试报告",5),
("部署、培训与文档","客户环境部署与验收支持；操作培训 1 次；用户使用手册、部署说明、接口/维护文档",3),
]
first=r
for i,(m,w,d) in enumerate(items,1):
    ws.cell(r,1,i); ws.cell(r,2,m); ws.cell(r,3,w); ws.cell(r,4,d); ws.cell(r,5,f"={RATE}"); ws.cell(r,6,f"=D{r}*E{r}")
    for col in range(1,7):
        c=ws.cell(r,col); c.border=B; c.font=f(10); c.alignment=wrap
    for col in (1,4): ws.cell(r,col).alignment=ctr
    ws.cell(r,2).font=f(10,True)
    ws.cell(r,5).number_format=MONEY; ws.cell(r,6).number_format=MONEY
    ws.row_dimensions[r].height=max(30,16*(len(w)//28+1)+8); r+=1
last=r-1
def totrow(label,dformula,fformula,bold=True,fill=TOT,note=None):
    global r
    ws.merge_cells(start_row=r,start_column=1,end_row=r,end_column=3)
    ws.cell(r,1,label); ws.cell(r,1).alignment=rgt
    if dformula: ws.cell(r,4,dformula)
    if note: ws.cell(r,5,note)
    ws.cell(r,6,fformula)
    for col in range(1,7):
        c=ws.cell(r,col); c.border=B; c.font=f(10,bold); c.fill=fill
    ws.cell(r,4).alignment=ctr; ws.cell(r,5).alignment=ctr; ws.cell(r,6).number_format=MONEY
    ws.row_dimensions[r].height=22; rr=r; r+=1; return rr
sub=totrow("开发费用合计（不含税）",f"=SUM(D{first}:D{last})",f"=SUM(F{first}:F{last})")
taxsel=totrow("是否开具增值税专用发票（含税 6%，可选；下拉选择 是/否）→",None,None,fill=PatternFill(fill_type=None),bold=False)
ws.cell(taxsel,6,"是"); ws.cell(taxsel,6).alignment=ctr; ws.cell(taxsel,6).font=f(10,True,"C00000"); ws.cell(taxsel,6).number_format="@"
dv=DataValidation(type="list",formula1='"是,否"',allow_blank=False); ws.add_data_validation(dv); dv.add(ws.cell(taxsel,6))
tax=totrow("税费（增值税 6%，可选）",None,f'=IF(F{taxsel}="是",ROUND(F{sub}*6%,2),0)',bold=False,fill=PatternFill(fill_type=None),note="6%")
grand=totrow("报价总计（含税）",None,f"=F{sub}+F{tax}")
ws.cell(grand,6).font=f(11,True,"C00000")
ws.merge_cells(start_row=r,start_column=1,end_row=r,end_column=3); ws.cell(r,1,"总计金额（大写）").alignment=rgt; ws.cell(r,1).font=f(10,True)
ws.merge_cells(start_row=r,start_column=4,end_row=r,end_column=6)
ws.cell(r,4,f'=IF(F{taxsel}="是","人民币柒万陆仟叁佰贰拾元整","人民币柒万贰仟元整")')
for col in range(1,7): ws.cell(r,col).border=B
ws.cell(r,4).font=f(10,True); ws.cell(r,4).alignment=ctr; capsrow=r; r+=2

section("二、可选增值项（不计入上述合计，按需另行确认）")
header()
opts=[
("平台适配器－雨桐（可选/另议）","验证码平台对接：登录验证码处理、会话维护、询价接口对接、结果标准化。因验证码机制复杂度未定，工作量为预估，最终另议",5,None),
("轨迹查询","按运单号跨平台查询物流轨迹，统一轨迹节点展示，支持批量查询与导出",6,None),
("价格监控与降价提醒","设定线路/重量段监控任务，定时询价记录价格走势，低于阈值或降价时桌面通知/邮件提醒",6,None),
("多用户权限与云端部署","多用户登录、角色权限（管理员/操作员）、账号共享与审计日志；后端云服务器部署（Docker）、HTTPS 与数据备份",12,None),
("新增平台单价（按平台计）","每新增 1 个货代平台适配器（常规登录+询价接口）；含验证码或签名逆向等复杂平台另行评估",4,"元/平台"),
]
of=r
for i,(m,w,d,unit) in enumerate(opts,1):
    ws.cell(r,1,f"A{i}"); ws.cell(r,2,m); ws.cell(r,3,w); ws.cell(r,4,d); ws.cell(r,5,f"={RATE}"); ws.cell(r,6,f"=D{r}*E{r}")
    for col in range(1,7):
        c=ws.cell(r,col); c.border=B; c.font=f(10); c.alignment=wrap; c.fill=OPT
    for col in (1,4): ws.cell(r,col).alignment=ctr
    ws.cell(r,2).font=f(10,True); ws.cell(r,5).number_format=MONEY; ws.cell(r,6).number_format=MONEY
    ws.row_dimensions[r].height=max(30,16*(len(w)//28+1)+8); r+=1
ws.merge_cells(start_row=r,start_column=1,end_row=r,end_column=6)
ws.cell(r,1,"注：可选增值项价格为不含税参考价，如选用将另签补充协议或合并入合同；“新增平台单价”为每个平台的单价。").font=f(9,c="595959"); r+=2

def textblock(title,lines):
    global r
    section(title)
    for ln in lines:
        ws.merge_cells(start_row=r,start_column=1,end_row=r,end_column=6)
        c=ws.cell(r,1,ln); c.font=f(10); c.alignment=Alignment(wrap_text=True,vertical="top")
        ws.row_dimensions[r].height=16*(len(ln)//62+1)+2; r+=1
    r+=1
textblock("三、付款方式",[
f"1. 首付款 40%：合同签订后 5 个工作日内支付，金额 = 报价总计 × 40%。",
"2. 中期款 40%：核心功能（后端框架 + 6 个平台适配器 + 桌面客户端）完成并演示确认后 5 个工作日内支付。",
"3. 尾款 20%：系统验收通过（含 50 项端到端测试通过、安装包交付、培训完成）后 5 个工作日内支付。",
])
# payment amounts table
for lab,p in [("首付款（40%）",0.4),("中期款（40%）",0.4),("尾款（20%）",0.2)]:
    ws.merge_cells(start_row=r,start_column=2,end_row=r,end_column=5)
    ws.cell(r,2,lab).alignment=rgt; ws.cell(r,6,f"=ROUND(F{grand}*{p},2)"); ws.cell(r,6).number_format=MONEY
    for col in range(2,7): ws.cell(r,col).border=B; ws.cell(r,col).font=f(10)
    r+=1
r+=1
textblock("四、交付周期",[
"1. 总工期：约 7 周（35 个工作日），自首付款到账且甲方提供各平台测试账号之日起计算；团队配置约 2 名开发 + 测试/项目协调兼任。",
"2. 里程碑：第 1 周 需求调研与接口分析；第 2–4 周 后端框架与平台适配器；第 3–5 周 桌面客户端 UI 与账号/历史管理；第 6 周 打包安装与端到端测试；第 7 周 部署、培训、验收。",
"3. 因甲方原因（账号/资料提供延迟、需求变更）或第三方平台接口变动导致的延期，工期相应顺延。",
])
textblock("五、售后维护",[
"1. 免费维护期：验收通过之日起 3 个月，免费提供缺陷修复、远程技术支持，以及因第三方平台小幅改版导致的适配器修复。",
"2. 年度维护：免费期满后可选购年度维护服务，费用为开发费用（不含税）的 15%/年，含平台接口变更适配、缺陷修复、远程支持（工作日 9:00–18:00 响应）。",
f"3. 年度维护费参考：见下方公式计算（含税与否同主报价口径）。",
])
ws.merge_cells(start_row=r,start_column=2,end_row=r,end_column=5)
ws.cell(r,2,"年度维护费（开发费不含税 × 15%）").alignment=rgt; ws.cell(r,6,f"=ROUND(F{sub}*15%,2)"); ws.cell(r,6).number_format=MONEY
for col in range(2,7): ws.cell(r,col).border=B; ws.cell(r,col).font=f(10,True)
r+=2
textblock("六、说明条款",[
"1. 本报价为参考报价，人天单价参考国内软件外包市场行情（1,200 元/人天），最终价格可根据需求确认结果、功能范围调整及双方协商进行调整。",
"2. 报价范围以本单“开发明细”所列内容为准；超出范围的新增需求或变更，按 1,200 元/人天另行评估计费。",
"3. 第三方平台（各货代网站/接口）的可用性、改版、风控策略及验证码机制不受乙方控制；如平台关闭接口或采取技术限制导致无法对接，双方协商调整方案或费用。",
"4. 甲方需提供各平台合法有效的测试/生产账号，并确保对接行为符合各平台服务条款；乙方仅按甲方授权进行技术对接。",
"5. 报价不含：服务器/云资源费用、第三方验证码识别服务费、代码签名证书、各平台账号或 API 使用费等第三方费用。",
"6. 交付物：Windows 安装包、源代码（尾款结清后交付）、需求与接口分析文档、用户手册、部署说明、测试报告。",
"7. 税费：默认按含 6% 增值税开票计算；如不需开票，可将“是否开具增值税专用发票”选为“否”，总计自动更新。",
"8. 本报价单有效期 30 天（2026-10-31 至 2026-11-30），经双方确认后以正式合同为准。",
])
r+=1
ws.cell(r,1,"供方（盖章）：").font=f(10,True); ws.cell(r,4,"客户（盖章）：").font=f(10,True); r+=2
ws.cell(r,1,"代表签字：").font=f(10); ws.cell(r,4,"代表签字：").font=f(10); r+=1
ws.cell(r,1,"日期：").font=f(10); ws.cell(r,4,"日期：").font=f(10)
ws.page_setup.paperSize=ws.PAPERSIZE_A4; ws.page_setup.orientation="portrait"
ws.page_setup.fitToWidth=1; ws.page_setup.fitToHeight=0; ws.sheet_properties.pageSetUpPr.fitToPage=True
ws.page_margins.left=ws.page_margins.right=0.4; ws.page_margins.top=ws.page_margins.bottom=0.5
ws.print_options.horizontalCentered=True
ws.oddFooter.center.text="第 &P 页 / 共 &N 页"
ws.sheet_view.showGridLines=False
wb.save("/workspace/freight-hub/quote/FreightHub报价单.xlsx")
print(sub,taxsel,tax,grand)
