const http = require('http');
const ST_PORT = 8001;
const PREFIX = process.env.ST_PATH_PREFIX || '';
const LISTEN_PORT = 8000;

if (!PREFIX) {
    console.error('[proxy] ST_PATH_PREFIX env var is required');
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
        // Inject <base> tag for relative URLs
        if (!/<base\s/i.test(r)) {
            r = r.replace(/<head[^>]*>/i, match => match + `<base href="${PREFIX_WITH_TRAILING}">`);
        }
    }
    if (contentType.includes('text/css')) {
        r = r.replace(/url\((["']?)\/(?!\/)/g, `url($1${PREFIX_WITH_TRAILING}`);
    }
    if (contentType.includes('javascript')) {
        r = r.replace(/(["'`])\/(api|scripts|css|fonts|images|themes|webfonts|backgrounds|img|assets|thumbnail|backups|modifiers|objects|sprites|sounds)\//g,
            `$1${PREFIX_WITH_TRAILING}$2/`);
        r = r.replace(/(["'`])\/(favicon\.)/g, `$1${PREFIX_WITH_TRAILING}$2`);
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
            let body = '';
            proxyRes.on('data', chunk => body += chunk.toString());
            proxyRes.on('end', () => {
                body = rewriteBody(body, ct);
                const buf = Buffer.from(body, 'utf-8');
                const h = Object.assign({}, proxyRes.headers);
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
