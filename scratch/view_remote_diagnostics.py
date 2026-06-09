import paramiko
import sys

def main():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect('43.98.170.61', username='root', password='ranAI3621715280', timeout=10)
    except Exception as e:
        print(f"❌ Failed to connect: {e}")
        sys.exit(1)

    print("✅ SSH Connected. Fetching remote diagnostics file...")
    stdin, stdout, stderr = client.exec_command("cat /root/ran_ai/diagnostics/diagnostics_history.json")
    content = stdout.read().decode().strip()
    err = stderr.read().decode().strip()
    
    if err:
        print(f"❌ Error: {err}")
    else:
        print(f"📄 File Contents: '{content}'")
        
    client.close()

if __name__ == '__main__':
    main()
