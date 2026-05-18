import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

export function middleware(req: NextRequest) {
  // Allow access to public assets and questionnaire endpoints without auth
  if (
    req.nextUrl.pathname.startsWith('/q/') || 
    req.nextUrl.pathname.startsWith('/_next') ||
    req.nextUrl.pathname.includes('.') // public files like favicon.ico
  ) {
    return NextResponse.next();
  }

  const basicAuth = req.headers.get('authorization');
  
  if (basicAuth) {
    const authValue = basicAuth.split(' ')[1];
    const [user, pwd] = atob(authValue).split(':');

    // The requested simple password
    if (user === 'admin' && pwd === '123') {
      return NextResponse.next();
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
  matcher: ['/((?!api|_next/static|_next/image|favicon.ico).*)'],
};
