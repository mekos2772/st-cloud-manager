// ST Path Proxy — wraps SillyTavern under a path prefix
//   node proxy.js <instanceId> <proxyPort> <stPort> [pathPrefix]
const http = require('http');
const zlib = require('zlib');

const INSTANCE_ID = process.argv[2] || process.env.ST_INSTANCE_ID;
const PROXY_PORT = parseInt(process.argv[3]) || parseInt(process.env.ST_PROXY_PORT) || 8000;
const ST_PORT = parseInt(process.argv[4]) || parseInt(process.env.ST_PORT) || 8001;
// Use explicit pathPrefix if provided, otherwise construct from instanceId
const PREFIX = process.argv[5] || process.env.ST_PATH_PREFIX || `/st-${INSTANCE_ID}`;
const PREFIX_SLASH = PREFIX.endsWith('/') ? PREFIX : `${PREFIX}/`;

if (!INSTANCE_ID) { console.error('Usage: proxy.js <instanceId> <proxyPort> <stPort> [pathPrefix]'); process.exit(1); }

console.log(`[proxy] ${INSTANCE_ID} :${PROXY_PORT}${PREFIX_SLASH} -> :${ST_PORT}/`);

// ── Response body rewriting ──────────────────────────────────────

const REWRITE_CT = ['text/html', 'text/css', 'application/javascript',
    'application/x-javascript', 'text/javascript'];

function shouldRewrite(ct) {
    if (!ct) return false;
    return REWRITE_CT.some(t => ct.includes(t));
}

function rewriteBody(buf, ct) {
    let text = buf.toString('utf8');

    if (ct.includes('text/html')) {
        // All attributes that carry URLs
        text = text.replace(/(\s)(src|href|content|data-src|data-href|action|poster|data|cite|formaction|manifest)=(["'])\/(?!\/)/g,
            `$1$2=$3${PREFIX_SLASH}`);
        // <link rel=modulepreload href="/...">
        // Already covered by href= above
        // <base> injection
        if (!/<base\s/i.test(text)) {
            text = text.replace(/<head[^>]*>/i, m => m + `<base href="${PREFIX_SLASH}">`);
        }
    }

    if (ct.includes('text/css')) {
        // url(/...) and url("/...") and url('/...')
        text = text.replace(/url\((["']?)\/(?!\/)/g, `url($1${PREFIX_SLASH}`);
    }

    if (ct.includes('javascript')) {
        // String-literal paths — comprehensive regex catches:
        // fetch("/..."), new URL("/..."), import("/..."), Worker("/..."),
        // EventSource("/..."), SharedWorker("/..."), XMLHttpRequest("/..."),
        // navigator.serviceWorker.register("/...")
        // Generic pattern: a quote followed by /subdir/...
        text = text.replace(/(["'`])\/([a-zA-Z][a-zA-Z0-9._-]*\/)/g,
            `$1${PREFIX_SLASH}$2`);
        // Root-level files
        text = text.replace(/(["'`])\/(style\.css|script\.js|favicon\.ico|manifest\.json|robots\.txt|login\.html)/g,
            `$1${PREFIX_SLASH}$2`);
        // socket.io standalone (various formats)
        text = text.replace(/(["'`])(\/socket\.io)/g, `$1${PREFIX_SLASH}/socket.io`);
        // import() with string literal
        text = text.replace(/import\((["'`])\/(?!\/)/g, `import($1${PREFIX_SLASH}`);
        // new Worker("/
        text = text.replace(/new\s+(Worker|SharedWorker)\((["'`])\/(?!\/)/g,
            `new $1($2${PREFIX_SLASH}`);
        // navigator.serviceWorker.register("/
        text = text.replace(/(register)\((["'`])\/(?!\/)/g,
            `$1($2${PREFIX_SLASH}`);
    }

    return text;
}

// ── HTTP server ─────────────────────────────────────────────────

const server = http.createServer((req, res) => {
    // Redirect /st-xxx (no slash) → /st-xxx/
    if (req.url === PREFIX) {
        res.writeHead(301, { 'Location': PREFIX_SLASH });
        res.end();
        return;
    }

    // Must be under our prefix
    if (!req.url.startsWith(PREFIX_SLASH)) {
        res.writeHead(404);
        res.end('Not Found');
        return;
    }

    // Strip prefix for ST
    const stPath = req.url.slice(PREFIX.length) || '/';

    const proxyReq = http.request({
        hostname: '127.0.0.1',
        port: ST_PORT,
        path: stPath,
        method: req.method,
        headers: req.headers,
    }, (proxyRes) => {
        const ct = proxyRes.headers['content-type'] || '';

        // Rewrite Location header
        if (proxyRes.headers['location'] && proxyRes.headers['location'].startsWith('/')) {
            proxyRes.headers['location'] = PREFIX + proxyRes.headers['location'];
        }

        if (shouldRewrite(ct)) {
            // Decompress if needed, rewrite, send uncompressed
            const chunks = [];
            proxyRes.on('data', c => chunks.push(c));
            proxyRes.on('end', () => {
                let raw = Buffer.concat(chunks);
                const enc = (proxyRes.headers['content-encoding'] || '').toLowerCase();
                if (enc === 'gzip' || enc === 'deflate' || enc === 'br') {
                    try {
                        raw = enc === 'gzip' ? zlib.gunzipSync(raw)
                            : enc === 'deflate' ? zlib.inflateSync(raw)
                            : zlib.brotliDecompressSync(raw);
                    } catch (e) { /* pass through */ }
                }
                const body = rewriteBody(raw, ct);
                const buf = Buffer.from(body, 'utf8');
                const h = Object.assign({}, proxyRes.headers);
                delete h['content-encoding'];
                delete h['transfer-encoding'];
                h['content-length'] = buf.length.toString();
                // Prevent CDN caching of auth-gated responses
                h['cache-control'] = 'private, no-cache';
                res.writeHead(proxyRes.statusCode, h);
                res.end(buf);
            });
        } else {
            res.writeHead(proxyRes.statusCode, proxyRes.headers);
            proxyRes.pipe(res);
        }
    });

    proxyReq.on('error', () => { res.writeHead(502); res.end('Bad Gateway'); });
    req.pipe(proxyReq);
});

// WebSocket upgrade
server.on('upgrade', (req, socket, head) => {
    let stPath = req.url;
    if (stPath.startsWith(PREFIX)) stPath = stPath.slice(PREFIX.length) || '/';

    const proxyReq = http.request({
        hostname: '127.0.0.1',
        port: ST_PORT,
        path: stPath,
        method: req.method,
        headers: req.headers,
    });

    proxyReq.on('upgrade', (proxyRes, proxySocket, proxyHead) => {
        socket.write('HTTP/1.1 101 Switching Protocols\r\n' +
            Object.keys(proxyRes.headers).map(k => `${k}: ${proxyRes.headers[k]}`).join('\r\n') +
            '\r\n\r\n');
        proxySocket.pipe(socket);
        socket.pipe(proxySocket);
    });

    proxyReq.on('error', () => socket.destroy());
    proxyReq.end();
});

server.listen(PROXY_PORT, '127.0.0.1', () => {
    console.log(`[proxy] running on :${PROXY_PORT}`);
});
