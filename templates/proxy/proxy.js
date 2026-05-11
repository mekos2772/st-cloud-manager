const http = require('http');
const zlib = require('zlib');
const ST_PORT = parseInt(process.env.ST_PORT) || 8001;
const LISTEN_PORT = parseInt(process.env.ST_PROXY_PORT) || 8000;

let raw = process.env.ST_PATH_PREFIX || '';
// MSYS2/Git Bash mangles Unix paths to Windows paths (e.g. /st-xxx -> C:/Program Files/Git/st-xxx)
// Extract the suffix after the last colon-slash or drive letter
if (raw.includes(':\\') || raw.includes(':/')) {
    const m = raw.match(/[a-zA-Z]:[\\\/].*?(\/st-[a-z0-9]+)$/i);
    if (m) raw = m[1];
    else raw = '/' + raw.replace(/^.*[\\\/]/, '');
}
const PREFIX = raw;
if (!PREFIX || !PREFIX.startsWith('/')) {
    console.error('[proxy] ST_PATH_PREFIX must start with /, got:', process.env.ST_PATH_PREFIX);
    process.exit(1);
}

const PREFIX_NO_TRAILING = PREFIX.replace(/\/$/, '');
const PREFIX_WITH_TRAILING = PREFIX_NO_TRAILING + '/';

const REWRITE_TYPES = ['text/html', 'text/css', 'application/javascript',
    'application/x-javascript', 'text/javascript'];

function shouldRewrite(ct) {
    if (!ct) return false;
    return REWRITE_TYPES.some(t => ct.includes(t));
}

function rewriteBody(body, contentType) {
    let r = body;
    if (contentType.includes('text/html')) {
        // Rewrite absolute paths in attributes
        r = r.replace(/(\s)(src|href|content|data-src|data-href)=(["'])\/(?!\/)/g,
            `$1$2=$3${PREFIX_WITH_TRAILING}`);
        // Inject <base> tag (use relative path to avoid MSYS2 path mangling)
        if (!/<base\s/i.test(r)) {
            const relBase = PREFIX_NO_TRAILING.replace(/^\/+/, '') + '/';
            r = r.replace(/<head[^>]*>/i, match => match + `<base href="/${relBase}">`);
        }
    }
    if (contentType.includes('text/css')) {
        r = r.replace(/url\((["']?)\/(?!\/)/g, `url($1${PREFIX_WITH_TRAILING}`);
    }
    if (contentType.includes('javascript')) {
        // Root-level assets (ST 1.18 style): /style.css /script.js /favicon.ico etc.
        r = r.replace(/(["'`])\/(style\.css|script\.js|favicon\.ico|manifest\.json|robots\.txt|login\.html)/g,
            `$1${PREFIX_WITH_TRAILING}$2`);
        // Subdirectory paths
        r = r.replace(/(["'`])\/(api|scripts|css|fonts|images|themes|webfonts|backgrounds|img|assets|thumbnail|thumbnails|backups|modifiers|objects|sprites|sounds|socket\.io|characters|vectors|user|extensions|locales|lib)\//g,
            `$1${PREFIX_WITH_TRAILING}$2/`);
        // Socket.IO standalone
        r = r.replace(/(["'`])(\/socket\.io)/g, `$1${PREFIX_WITH_TRAILING}/socket.io`);
    }
    return r;
}

const server = http.createServer((req, res) => {
    // Traefik already stripped the prefix — proxy passes through to ST directly
    const proxyReq = http.request({
        hostname: '127.0.0.1',
        port: ST_PORT,
        path: req.url,
        method: req.method,
        headers: req.headers,
    }, (proxyRes) => {
        const ct = proxyRes.headers['content-type'] || '';
        if (shouldRewrite(ct)) {
            const chunks = [];
            proxyRes.on('data', chunk => chunks.push(chunk));
            proxyRes.on('end', () => {
                let raw = Buffer.concat(chunks);
                const h = Object.assign({}, proxyRes.headers);

                // Decompress if ST sent gzip — we need plain text to rewrite paths
                const enc = (h['content-encoding'] || '').toLowerCase();
                if (enc === 'gzip' || enc === 'deflate' || enc === 'br') {
                    try {
                        raw = enc === 'gzip' ? zlib.gunzipSync(raw)
                            : enc === 'deflate' ? zlib.inflateSync(raw)
                            : zlib.brotliDecompressSync(raw);
                        delete h['content-encoding'];
                    } catch (e) {
                        // Decompress failed — send raw, skip rewrite
                        h['content-length'] = raw.length.toString();
                        delete h['transfer-encoding'];
                        res.writeHead(proxyRes.statusCode, h);
                        res.end(raw);
                        return;
                    }
                }

                let body = raw.toString('utf-8');
                body = rewriteBody(body, ct);
                const buf = Buffer.from(body, 'utf-8');
                h['content-length'] = buf.length.toString();
                delete h['transfer-encoding'];
                res.writeHead(proxyRes.statusCode, h);
                res.end(buf);
            });
        } else {
            res.writeHead(proxyRes.statusCode, proxyRes.headers);
            proxyRes.pipe(res);
        }
    });
    proxyReq.on('error', () => { res.writeHead(502); res.end('Proxy Error'); });
    req.pipe(proxyReq);
});

server.on('upgrade', (req, socket, head) => {
    const proxyReq = http.request({
        hostname: '127.0.0.1',
        port: ST_PORT,
        path: req.url,
        method: req.method,
        headers: req.headers,
    });
    proxyReq.on('upgrade', (proxyRes, proxySocket, proxyHead) => {
        proxySocket.write(head);
        socket.write('HTTP/1.1 101 Switching Protocols\r\n' +
            Object.keys(proxyRes.headers).map(k => `${k}: ${proxyRes.headers[k]}`).join('\r\n') +
            '\r\n\r\n');
        proxySocket.pipe(socket);
        socket.pipe(proxySocket);
    });
    proxyReq.on('error', () => socket.destroy());
    proxyReq.end();
});

server.listen(LISTEN_PORT, () => {
    console.log(`[proxy] :${LISTEN_PORT} -> :${ST_PORT}, prefix=${PREFIX}`);
});
