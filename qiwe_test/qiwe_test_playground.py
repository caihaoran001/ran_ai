import urllib.request
import json
import time
import os
import base64
import sys

# Ensure UTF-8 printing
sys.stdout.reconfigure(encoding='utf-8')

class QiWePlayground:
    def __init__(self, token, base_url="http://manager.qiweapi.com/qiwe"):
        self.token = token
        self.base_url = base_url
        self.headers = {
            "X-QIWEI-TOKEN": self.token,
            "Content-Type": "application/json"
        }
        self.session_file = "qiwe_session_cache.json"
        self.guid = self.load_session()

    def load_session(self):
        paths = [self.session_file, os.path.join("qiwe_test", self.session_file)]
        for p in paths:
            if os.path.exists(p):
                try:
                    with open(p, "r") as f:
                        data = json.load(f)
                        return data.get("guid")
                except:
                    pass
        return None

    def save_session(self, guid):
        self.guid = guid
        paths = [self.session_file, os.path.join("qiwe_test", self.session_file)]
        for p in paths:
            try:
                # Ensure directory exists if path contains it
                dir_name = os.path.dirname(p)
                if dir_name and not os.path.exists(dir_name):
                    os.makedirs(dir_name)
                with open(p, "w") as f:
                    json.dump({"guid": guid}, f)
            except:
                pass

    def request(self, method, params):
        url = f"{self.base_url}/api/qw/doApi"
        payload = json.dumps({"method": method, "params": params}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers=self.headers, method="POST")
        try:
            with urllib.request.urlopen(req) as res:
                response_data = res.read().decode('utf-8')
                return json.loads(response_data)
        except Exception as e:
            return {"code": -1, "msg": f"HTTP Error: {str(e)}"}

    def create_device(self):
        print("\n[1/4] 🚀 正在创建虚拟设备实例...")
        params = {
            "deviceName": "QiWe_Test_iPad",
            "deviceType": 0,
            "areaCode": 320000,
            "proxyUrl": "",
            "clientVersion": ""
        }
        res = self.request("/client/createClient", params)
        if res.get("code") == 0:
            guid = res.get("data", {}).get("guid")
            self.save_session(guid)
            print(f"✅ 设备实例创建成功！\n👉 GUID: {guid}")
            return guid
        else:
            print(f"❌ 设备创建失败: {res.get('msg')}")
            return None

    def get_login_qr(self):
        print("\n[2/4] 📲 正在获取登录二维码...")
        res = self.request("/login/getLoginQrcode", {"guid": self.guid, "useCache": True})
        if res.get("code") == 0:
            qr_base64 = res.get("data", {}).get("qrCodeBase64", "")
            if "," in qr_base64:
                qr_base64 = qr_base64.split(",")[1]
            
            img_path = "qiwe_login_qr.png"
            with open(img_path, "wb") as f:
                f.write(base64.b64decode(qr_base64))
            
            print(f"🎉 二维码已成功保存至本地工作区！")
            print(f"📂 文件路径: {os.path.abspath(img_path)}")
            print(f"💡 【提示】请在当前文件管理器中打开 {img_path}，使用企业微信扫码登录。")
            return True
        else:
            print(f"❌ 获取二维码失败: {res.get('msg')}")
            return False

    def poll_login_status(self):
        print("\n[3/4] ⏳ 正在轮询等待扫码登录状态...")
        while True:
            res = self.request("/login/checkLoginQrCode", {"guid": self.guid})
            if res.get("code") == 0:
                data = res.get("data", {})
                status = data.get("status")
                if status == 2:
                    print("🎉 恭喜！扫码成功，设备已成功上线！")
                    return True
                elif status == 1:
                    print("👀 检测到已扫码，等待手机端确认登录...")
                elif status == 10:
                    print("⚠️ 登录需输入手机验证码，请在控制台处理。")
                else:
                    print("⏳ 等待扫码中...")
            else:
                print(f"❌ 查询登录状态发生异常: {res.get('msg')}")
            time.sleep(3)

    def send_test_message(self):
        print("\n[4/4] 💬 设备已在线，进入消息测试环节。")
        told = input("请输入接收人的企业微信/个人微信ID (toId/external_userid): ").strip()
        content = input("请输入要发送的消息内容: ").strip()
        
        if not told or not content:
            print("🚫 接收人或内容不能为空，测试退出。")
            return

        res = self.request("/msg/sendText", {
            "guid": self.guid,
            "told": told,
            "content": content
        })
        if res.get("code") == 0:
            print("🚀 消息发送成功！请检查接收端是否收到消息！")
        else:
            print(f"❌ 消息发送失败: {res.get('msg')}")

def main():
    print("=" * 60)
    print("           QiWe 开放平台独立能力测试游乐场           ")
    print("=" * 60)
    
    token = input("请输入您的 QiWe X-QIWEI-TOKEN: ").strip()
    if not token:
        print("🚫 Token 不能为空，请在控制台获取后重试。")
        return
        
    playground = QiWePlayground(token)
    
    if not playground.guid:
        guid = playground.create_device()
        if not guid: return
    else:
        print(f"ℹ️ 检测到本地已存在缓存 GUID: {playground.guid}")
        use_cache = input("是否复用此设备？(y/n, 默认为 y): ").strip().lower()
        if use_cache == 'n':
            guid = playground.create_device()
            if not guid: return

    if not playground.get_login_qr():
        return

    if not playground.poll_login_status():
        return

    playground.send_test_message()

if __name__ == "__main__":
    main()
