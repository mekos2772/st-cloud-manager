// ST Path Proxy
//   node proxy.js <instanceId> <proxyPort> <stPort> [pathPrefix]
const http = require('http');
const zlib = require('zlib');

const INSTANCE_ID = process.argv[2] || process.env.ST_INSTANCE_ID;
const PROXY_PORT = parseInt(process.argv[3], 10) || parseInt(process.env.ST_PROXY_PORT, 10) || 8000;
const ST_PORT = parseInt(process.argv[4], 10) || parseInt(process.env.ST_ST_PORT, 10) || 8001;
const PREFIX = process.argv[5] || process.env.ST_PATH_PREFIX || `/st-${INSTANCE_ID}`;
const PREFIX_SLASH = PREFIX.endsWith('/') ? PREFIX : `${PREFIX}/`;

if (!INSTANCE_ID) {
    console.error('Usage: proxy.js <instanceId> <proxyPort> <stPort> [pathPrefix]');
    process.exit(1);
}

console.log(`[proxy] ${INSTANCE_ID} :${PROXY_PORT}${PREFIX_SLASH} -> :${ST_PORT}/`);

const REWRITE_CT = [
    'text/html',
    'text/css',
    'application/javascript',
    'application/x-javascript',
    'text/javascript',
];

function headerValue(value) {
    if (Array.isArray(value)) return value.join('; ');
    return value ? String(value) : '';
}

function shouldRewrite(contentType) {
    const ct = headerValue(contentType);
    return REWRITE_CT.some((t) => ct.includes(t));
}

function safeEnd(res, statusCode, headers, body = '') {
    if (res.destroyed) return;
    try {
        if (!res.headersSent) {
            res.writeHead(statusCode, headers);
        }
        res.end(body);
    } catch (err) {
        console.error('[proxy] response write failed:', err && err.stack ? err.stack : err);
        try { res.destroy(); } catch (_) {}
    }
}

function decodeBody(raw, encoding) {
    if (!encoding) return raw;
    if (encoding === 'gzip') return zlib.gunzipSync(raw);
    if (encoding === 'deflate') return zlib.inflateSync(raw);
    if (encoding === 'br') return zlib.brotliDecompressSync(raw);
    return raw;
}

function rewriteBody(buf, contentType) {
    const ct = headerValue(contentType);
    let text = buf.toString('utf8');

    if (ct.includes('text/html')) {
        text = text.replace(
            /(\s)(src|href|content|data-src|data-href|action|poster|data|cite|formaction|manifest)=(["'])\/(?!\/)/g,
            `$1$2=$3${PREFIX_SLASH}`,
        );
        if (!/<base\s/i.test(text)) {
            text = text.replace(/<head[^>]*>/i, (m) => `${m}<base href="${PREFIX_SLASH}">`);
        }
    }

    if (ct.includes('text/css')) {
        text = text.replace(/url\((["']?)\/(?!\/)/g, `url($1${PREFIX_SLASH}`);
        text = text.replace(/@import\s+(["'])\/(?!\/)/g, `@import $1${PREFIX_SLASH}`);
    }

    if (ct.includes('javascript')) {
        text = text.replace(/(["'`])\/([a-zA-Z][a-zA-Z0-9._-]*\/)/g, `$1${PREFIX_SLASH}$2`);
        text = text.replace(
            /(["'`])\/(style\.css|script\.js|favicon\.ico|manifest\.json|robots\.txt|login\.html)/g,
            `$1${PREFIX_SLASH}$2`,
        );
        text = text.replace(/(["'`])\/socket\.io/g, `$1${PREFIX_SLASH}socket.io`);
        text = text.replace(/import\((["'`])\/(?!\/)/g, `import($1${PREFIX_SLASH}`);
        text = text.replace(/new\s+(Worker|SharedWorker)\((["'`])\/(?!\/)/g, `new $1($2${PREFIX_SLASH}`);
        text = text.replace(/(register)\((["'`])\/(?!\/)/g, `$1($2${PREFIX_SLASH}`);
        text = text.replace(/new\s+URL\((["'`])\/(?!\/)/g, `new URL($1${PREFIX_SLASH}`);
    }

    return Buffer.from(text, 'utf8');
}

function forwardHttp(req, res) {
    if (req.url === PREFIX) {
        safeEnd(res, 301, { Location: PREFIX_SLASH });
        return;
    }

    if (!req.url.startsWith(PREFIX_SLASH)) {
        safeEnd(res, 404, {}, 'Not Found');
        return;
    }

    const stPath = req.url.slice(PREFIX.length) || '/';
    const proxyReq = http.request(
        {
            hostname: '127.0.0.1',
            port: ST_PORT,
            path: stPath,
            method: req.method,
            headers: req.headers,
        },
        (proxyRes) => {
            const location = proxyRes.headers.location;
            if (typeof location === 'string' && location.startsWith('/')) {
                proxyRes.headers.location = PREFIX + location;
            }

            if (!shouldRewrite(proxyRes.headers['content-type'])) {
                try {
                    res.writeHead(proxyRes.statusCode || 502, proxyRes.headers);
                    proxyRes.pipe(res);
                } catch (err) {
                    console.error('[proxy] pass-through failed:', err && err.stack ? err.stack : err);
                    safeEnd(res, 502, {}, 'Bad Gateway');
                }
                return;
            }

            const chunks = [];
            proxyRes.on('data', (chunk) => chunks.push(chunk));
            proxyRes.on('error', (err) => {
                console.error('[proxy] upstream response error:', err && err.stack ? err.stack : err);
                safeEnd(res, 502, {}, 'Bad Gateway');
            });
            proxyRes.on('end', () => {
                try {
                    const raw = Buffer.concat(chunks);
                    const encoding = headerValue(proxyRes.headers['content-encoding']).toLowerCase();
                    const decoded = decodeBody(raw, encoding);
                    const body = rewriteBody(decoded, proxyRes.headers['content-type']);
                    const headers = { ...proxyRes.headers };
                    delete headers['content-encoding'];
                    delete headers['transfer-encoding'];
                    headers['content-length'] = String(body.length);
                    headers['cache-control'] = 'private, no-cache';
                    safeEnd(res, proxyRes.statusCode || 200, headers, body);
                } catch (err) {
                    console.error('[proxy] rewrite failed:', err && err.stack ? err.stack : err);
                    safeEnd(res, 502, {}, 'Bad Gateway');
                }
            });
        },
    );

    proxyReq.on('error', (err) => {
        console.error('[proxy] upstream request error:', err && err.stack ? err.stack : err);
        safeEnd(res, 502, {}, 'Bad Gateway');
    });

    req.on('aborted', () => {
        try { proxyReq.destroy(); } catch (_) {}
    });
    req.on('error', (err) => {
        console.error('[proxy] request stream error:', err && err.stack ? err.stack : err);
        try { proxyReq.destroy(); } catch (_) {}
    });
    res.on('error', (err) => {
        console.error('[proxy] response stream error:', err && err.stack ? err.stack : err);
        try { proxyReq.destroy(); } catch (_) {}
    });

    req.pipe(proxyReq);
}

const server = http.createServer((req, res) => {
    try {
        forwardHttp(req, res);
    } catch (err) {
        console.error('[proxy] request handler failed:', err && err.stack ? err.stack : err);
        safeEnd(res, 500, {}, 'Proxy Error');
    }
});

server.on('upgrade', (req, socket, head) => {
    try {
        let stPath = req.url || '/';
        if (stPath.startsWith(PREFIX)) stPath = stPath.slice(PREFIX.length) || '/';

        const proxyReq = http.request({
            hostname: '127.0.0.1',
            port: ST_PORT,
            path: stPath,
            method: req.method,
            headers: req.headers,
        });

        proxyReq.on('upgrade', (proxyRes, proxySocket, proxyHead) => {
            socket.write(
                'HTTP/1.1 101 Switching Protocols\r\n' +
                Object.keys(proxyRes.headers).map((k) => `${k}: ${proxyRes.headers[k]}`).join('\r\n') +
                '\r\n\r\n',
            );
            if (head && head.length) proxySocket.write(head);
            if (proxyHead && proxyHead.length) socket.write(proxyHead);
            proxySocket.pipe(socket);
            socket.pipe(proxySocket);
            proxySocket.on('error', () => socket.destroy());
            socket.on('error', () => proxySocket.destroy());
        });

        proxyReq.on('response', (proxyRes) => {
            console.warn(`[proxy] upgrade downgraded to HTTP ${proxyRes.statusCode} for ${req.url}`);
            socket.destroy();
        });
        proxyReq.on('error', (err) => {
            console.error('[proxy] upgrade request error:', err && err.stack ? err.stack : err);
            socket.destroy();
        });

        proxyReq.end();
    } catch (err) {
        console.error('[proxy] upgrade handler failed:', err && err.stack ? err.stack : err);
        socket.destroy();
    }
});

server.on('clientError', (err, socket) => {
    console.error('[proxy] client error:', err && err.stack ? err.stack : err);
    try { socket.destroy(); } catch (_) {}
});

server.on('error', (err) => {
    console.error('[proxy] server error:', err && err.stack ? err.stack : err);
});

process.on('unhandledRejection', (err) => {
    console.error('[proxy] unhandled rejection:', err && err.stack ? err.stack : err);
});

process.on('uncaughtException', (err) => {
    console.error('[proxy] uncaught exception:', err && err.stack ? err.stack : err);
});

server.listen(PROXY_PORT, '127.0.0.1', () => {
    console.log(`[proxy] running on :${PROXY_PORT}`);
});
