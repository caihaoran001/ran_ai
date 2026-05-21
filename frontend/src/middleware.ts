import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

export function middleware(req: NextRequest) {
  const pathname = req.nextUrl.pathname;
  // Allow access to public assets and questionnaire endpoints without auth.
  if (
    pathname.startsWith('/q/') ||
    (pathname.startsWith('/api/questionnaires/') && pathname.endsWith('/submit')) ||
    pathname.startsWith('/_next') ||
    (!pathname.startsWith('/api/') && pathname.includes('.')) // public files like favicon.ico
  ) {
    return NextResponse.next();
  }

  const basicAuth = req.headers.get('authorization');
  const expectedUser = process.env.ADMIN_USERNAME || 'admin';
  const expectedPassword = process.env.ADMIN_PASSWORD || 'change-me';
  
  if (basicAuth) {
    try {
      const [scheme, authValue] = basicAuth.split(' ');
      if (scheme !== 'Basic' || !authValue) {
        throw new Error('invalid authorization header');
      }
      const [user, pwd] = atob(authValue).split(':');

      if (user === expectedUser && pwd === expectedPassword) {
        return NextResponse.next();
      }
    } catch {
      // Fall through to the same 401 response for malformed credentials.
    }
  }

  return new NextResponse('Authentication required', {
    status: 401,
    headers: {
      'WWW-Authenticate': 'Basic realm="Secure Area"',
    },
  });
}

export const config = {
  matcher: ['/((?!_next/static|_next/image|favicon.ico).*)'],
};
