import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

async function proxy(request, { params }) {
  const { path } = await params;
  const internal = process.env.API_INTERNAL_URL || 'http://api:8080';
  const target = new URL(`/` + path.join('/'), internal);
  target.search = new URL(request.url).search;

  const headers = new Headers();
  const contentType = request.headers.get('content-type');
  const cookie = request.headers.get('cookie');
  if (contentType) headers.set('content-type', contentType);
  if (cookie) headers.set('cookie', cookie);

  let upstream;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === 'GET' || request.method === 'HEAD' ? undefined : await request.arrayBuffer(),
      cache: 'no-store',
      redirect: 'manual',
    });
  } catch {
    return NextResponse.json({ detail: 'The local API is unavailable. Start the Logchat stack and try again.' }, { status: 502 });
  }

  const responseHeaders = new Headers();
  const upstreamType = upstream.headers.get('content-type');
  if (upstreamType) responseHeaders.set('content-type', upstreamType);
  const setCookie = upstream.headers.get('set-cookie');
  if (setCookie) responseHeaders.set('set-cookie', setCookie);
  responseHeaders.set('cache-control', 'no-store');

  return new NextResponse(await upstream.arrayBuffer(), {
    status: upstream.status,
    headers: responseHeaders,
  });
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
