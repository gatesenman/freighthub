import time

import requests

from . import net as _net

from .base import Adapter, AdapterError, Query, make_row, to_float

BASE = "https://xinh.kingtrans.net"


class XinhangAdapter(Adapter):
    """欣航：只有公开报价网站（price.jsp），无需登录、无需授权，也没有账号/API 授权切换。"""
    id = "xinhang"
    mode_short_login = mode_short_api = "公开网页"
    name = "欣航"
    system = "Kingtrans 公开报价网站（price.jsp）"
    mode = "公开网站，无需授权：POST 表单并解析返回的 HTML"
    needs_account = False
    fixed_mode = "login"
    fixed_label = "公开网站，无需授权"
    login_fields = []
    api_fields = []
    env_note = "欣航只有公开报价网页，没有测试环境，也没有授权，只显示「正式环境」。"
    api_impl = False
    login_note = "公开网站，无需授权：查到的是官网公开价，不是协议价。"
    note = "公开网站，无需授权（http://xinh.kingtrans.net/price.jsp），查到的是公开价而不是协议价。"

    def login_quote(self, q: Query):
        form = {
            "clientkind": "", "corpid": "", "orderField": "amt", "orderDirection": "asc",
            "country": q.country, "rweight": f"{q.weight:g}", "post": q.postcode or "",
            "goodstype": "WPX",
            "len": f"{q.length:g}" if q.dims() else "",
            "width": f"{q.width:g}" if q.dims() else "",
            "height": f"{q.height:g}" if q.dims() else "",
        }
        last = None
        for i in range(_net.ATTEMPTS):      # 连接级失败 → 换全新会话整套重来（先 GET 取 cookie，再 POST，都是只读）
            s = requests.Session()
            s.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
            try:
                s.get(BASE + "/price.jsp", timeout=(_net.CONNECT_TIMEOUT, 25))
                r = s.post(BASE + "/WebPrice?action=list", data=form, timeout=(_net.CONNECT_TIMEOUT, 40))
                break
            except (requests.ConnectTimeout, requests.exceptions.ConnectionError) as e:
                last = e
                time.sleep(0.3 * (i + 1))
            except requests.RequestException as e:
                raise AdapterError(f"网络请求失败：{e}")
            finally:
                s.close()
        else:
            raise AdapterError(f"网络请求失败：{last}")
        if r.status_code != 200:
            raise AdapterError(f"欣航返回 HTTP {r.status_code}")
        from bs4 import BeautifulSoup  # 延迟导入：加快启动
        soup = BeautifulSoup(r.text, "html.parser")
        rows = []
        for dl in soup.select("dl.ks_dl_table1"):
            dd = [" ".join(d.get_text(" ", strip=True).split()) for d in dl.find_all("dd")]
            if len(dd) < 4:
                continue
            channel = dd[0].split("【")[0].strip()
            total = to_float(dd[2])
            if total is None:
                continue
            ccy = "CNY" if "人民币" in dd[3] else dd[3]
            rows.append(make_row(
                self.id, self.name, channel, total, currency=ccy, days=dd[4] if len(dd) > 4 else "",
                fees=[{"label": "结算重(kg)", "value": dd[1]}],
            ))
        if not rows and "目的地" in r.text and "您当前查询目的地" not in r.text:
            raise AdapterError("欣航没有返回报价（国家代码可能不被支持）")
        return rows

    def login_test(self):
        r = _net.get(BASE + "/price.jsp", timeout=20)
        return f"公开页面可访问（HTTP {r.status_code}），无需登录。"
