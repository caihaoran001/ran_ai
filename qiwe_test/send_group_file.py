import requests
import json
import os
import sys

# Ensure UTF-8 printing
sys.stdout.reconfigure(encoding='utf-8')

def load_env():
    env = {}
    paths = [".env", "../.env"]
    for p in paths:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, val = line.split("=", 1)
                        env[key.strip()] = val.strip()
            break
    return env

def main():
    # 默认目标群 ID 和文件路径
    default_chat_id = "10926518308089720"
    
    # 解析命令行参数
    chat_id = sys.argv[1] if len(sys.argv) > 1 else default_chat_id
    file_path = sys.argv[2] if len(sys.argv) > 2 else "qiwe_test/test_doc.txt"

    env = load_env()
    token = env.get("QIWEI_TOKEN")
    guid = env.get("QIWEI_GUID")
    
    if not token or not guid:
        print("❌ 错误: .env 中未找到 QIWEI_TOKEN 或 QIWEI_GUID!")
        sys.exit(1)

    # 如果文件不存在，自动创建一个测试文本文件
    if not os.path.exists(file_path) and file_path == "qiwe_test/test_doc.txt":
        os.makedirs("qiwe_test", exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("你好！这是一份来自燃人AI大模型测试的测试文档，用于验证 QiWe 平台发送文件功能是否完全通畅！")
        print(f"📝 自动创建了测试文档: {file_path}")

    if not os.path.exists(file_path):
        print(f"❌ 错误: 本地文件不存在: {file_path}")
        sys.exit(1)

    filename = os.path.basename(file_path)
    print("=" * 60)
    print("           QiWe 开放平台文件发送测试开始           ")
    print("=" * 60)
    print(f"目标聊天 ID: {chat_id}")
    print(f"准备发送文件: {filename} ({os.path.getsize(file_path)} 字节)")
    print("-" * 60)

    # -------------------------------------------------------------
    # 步骤 1: 上传本地文件到微信 CDN
    # -------------------------------------------------------------
    print("\n📤 步骤 1: 正在上传本地文件到微信 CDN (/cloud/cdnBigUpload)...")
    upload_url = "http://manager.qiweapi.com/qiwe/api/qw/doFileApi"
    
    # 构造 multipart/form-data 的数据
    form_data = {
        "method": (None, "/cloud/cdnBigUpload"),
        "guid": (None, guid),
        "fileType": (None, "5")
    }
    
    # 打开文件并关联进 multipart 数据中
    with open(file_path, "rb") as f:
        files = {
            "file": (filename, f, "application/octet-stream")
        }
        headers = {
            "X-QIWEI-TOKEN": token
        }
        
        try:
            response = requests.post(upload_url, data=form_data, files=files, headers=headers)
            upload_res = response.json()
        except Exception as e:
            print(f"❌ 上传请求异常: {str(e)}")
            sys.exit(1)

    if upload_res.get("code") == 0:
        file_data = upload_res.get("data", {})
        file_id = file_data.get("fileId")
        file_aes_key = file_data.get("fileAesKey")
        file_size = file_data.get("fileSize")
        returned_filename = file_data.get("filename")
        print("✅ 文件 CDN 上传成功！")
        print(f" 🔹 fileId: {file_id[:30]}... (长度 {len(file_id)})")
        print(f" 🔹 fileAesKey: {file_aes_key}")
        print(f" 🔹 fileSize: {file_size} 字节")
    else:
        print(f"❌ 上传 CDN 失败: {upload_res.get('msg')}")
        print("完整响应:", json.dumps(upload_res, indent=4, ensure_ascii=False))
        sys.exit(1)

    # -------------------------------------------------------------
    # 步骤 2: 调用 /msg/sendFile 接口将 CDN 文件发送给接收方
    # -------------------------------------------------------------
    print(f"\n🚀 步骤 2: 正在发送文件消息给接收方 {chat_id} (/msg/sendFile)...")
    send_url = "http://manager.qiweapi.com/qiwe/api/qw/doApi"
    
    payload = {
        "method": "/msg/sendFile",
        "params": {
            "guid": guid,
            "toId": chat_id,
            "fileAesKey": file_aes_key,
            "fileId": file_id,
            "fileSize": file_size,
            "filename": returned_filename
        }
    }
    
    send_headers = {
        "X-QIWEI-TOKEN": token,
        "Content-Type": "application/json"
    }

    try:
        send_response = requests.post(send_url, json=payload, headers=send_headers)
        send_res = send_response.json()
    except Exception as e:
        print(f"❌ 发送请求异常: {str(e)}")
        sys.exit(1)

    if send_res.get("code") == 0:
        print("✅ 文件消息发送成功！API 响应正文:")
        print(json.dumps(send_res, indent=4, ensure_ascii=False))
        print("\n🎉 恭喜！现在去微信群里查看，您应该已经收到了这个测试文件！")
    else:
        print(f"❌ 发送文件失败: {send_res.get('msg')}")
        print("完整响应:", json.dumps(send_res, indent=4, ensure_ascii=False))

    print("=" * 60)

if __name__ == "__main__":
    main()
