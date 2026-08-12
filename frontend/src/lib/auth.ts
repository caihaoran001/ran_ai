const SECRET = process.env.BACKEND_ADMIN_API_KEY || "default-secret-key-1234";

async function sha256(message: string): Promise<string> {
  const msgBuffer = new TextEncoder().encode(message);
  const hashBuffer = await crypto.subtle.digest("SHA-256", msgBuffer);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map(b => b.toString(16).padStart(2, "0")).join("");
}

export async function createSessionToken(username: string): Promise<string> {
  const expiresAt = Date.now() + 24 * 60 * 60 * 1000; // 24 hours
  const payload = `${username}:${expiresAt}`;
  const signature = await sha256(`${payload}:${SECRET}`);
  return `${payload}:${signature}`;
}

export async function verifySessionToken(token: string | undefined): Promise<boolean> {
  if (!token) return false;
  try {
    const parts = token.split(":");
    if (parts.length !== 3) return false;
    const [username, expiresAtStr, signature] = parts;
    const expiresAt = parseInt(expiresAtStr, 10);
    
    if (isNaN(expiresAt) || expiresAt < Date.now()) return false;
    
    const expectedUser = process.env.ADMIN_USERNAME || "admin";
    if (username !== expectedUser) return false;
    
    const payload = `${username}:${expiresAtStr}`;
    const expectedSignature = await sha256(`${payload}:${SECRET}`);
    
    return signature === expectedSignature;
  } catch {
    return false;
  }
}

export async function sessionUsername(token: string | undefined): Promise<string | null> {
  if (!(await verifySessionToken(token))) return null;
  return token?.split(":", 1)[0] ?? null;
}
