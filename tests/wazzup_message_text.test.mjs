import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { decodeMessageText, tokenizeMessageLinks } from '../src/components/wazzup/messageTextFormatting.js';

const require = createRequire(import.meta.url);
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');
const { build } = require('esbuild');
const cache = join(process.cwd(), 'node_modules/.cache/otp-tests');
mkdirSync(cache, { recursive: true });
const outfile = join(cache, 'wazzup-message-text.mjs');
await build({ entryPoints: ['src/components/wazzup/ChatMessageText.jsx'], outfile,
    bundle: true, platform: 'node', format: 'esm', external: ['react'] });
const { default: MessageText } = await import(pathToFileURL(outfile));

const textContent = (node) => {
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    if (Array.isArray(node)) return node.map(textContent).join('');
    return node?.props?.children == null ? '' : textContent(node.props.children);
};
const all = (node, type) => {
    if (Array.isArray(node)) return node.flatMap((child) => all(child, type));
    if (!node || typeof node !== 'object') return [];
    return [...(node.type === type ? [node] : []), ...all(node.props?.children, type)];
};

test('CRM escaped arrows, punctuation and numeric Unicode decode once as plain text', () => {
    assert.equal(decodeMessageText('Меню -&gt; Выйти &amp; &quot;Профиль&quot; &#62; &#x1F44D;'),
        'Меню -> Выйти & "Профиль" > 👍');
    assert.equal(decodeMessageText('&lt;текст&gt;&nbsp;&laquo;да&raquo; &mdash; &#10;'), '<текст>\u00a0«да» — \n');
    assert.equal(decodeMessageText('&amp;gt; &unknown; &#x110000; &#0; &#xD800; &gt'),
        '&gt; &unknown; &#x110000; &#0; &#xD800; &gt');
    assert.equal(decodeMessageText(null), '');
});

test('http, https and www links retain their displayed spelling and decoded query arguments', () => {
    const text = 'https://example.com/?a=1&amp;b=2\nHTTP://EXAMPLE.com/path www.example.kz/info';
    const tokens = tokenizeMessageLinks(text);
    assert.deepEqual(tokens.filter((token) => token.href), [
        { text: 'https://example.com/?a=1&b=2', href: 'https://example.com/?a=1&b=2' },
        { text: 'HTTP://EXAMPLE.com/path', href: 'HTTP://EXAMPLE.com/path' },
        { text: 'www.example.kz/info', href: 'https://www.example.kz/info' },
    ]);
    assert.equal(tokens.map((token) => token.text).join(''), decodeMessageText(text));
});

test('sentence punctuation and unmatched brackets stay outside links, balanced URL brackets stay inside', () => {
    const text = '(https://example.com/wiki/Task_(a)). [https://example.com/page?q=1], «www.example.kz»!';
    const tokens = tokenizeMessageLinks(text);
    assert.deepEqual(tokens.filter((token) => token.href).map((token) => token.text), [
        'https://example.com/wiki/Task_(a)', 'https://example.com/page?q=1', 'www.example.kz',
    ]);
    assert.equal(tokens.map((token) => token.text).join(''), text);
    assert.equal(tokenizeMessageLinks('http://[::1]:8080/a')[0].href, 'http://[::1]:8080/a');
});

test('invalid URLs, other protocols and www substrings in emails or paths remain ordinary text', () => {
    const text = 'javascript:alert(1) data:text/html,test ftp://example.com https:// www. www.example user@www.example.com /www.example.com abcwww.example.com';
    assert.deepEqual(tokenizeMessageLinks(text), [{ text }]);
    assert.deepEqual(tokenizeMessageLinks(''), []);
});

test('links use a new tab and isolation while text selection preserves decoded text and emoji', () => {
    const encoded = 'Меню -&gt; https://example.com/?a=1&amp;b=2 👩🏽‍💻\n  👍🏾';
    const tree = MessageText.type({ text: encoded });
    assert.equal(textContent(tree), decodeMessageText(encoded));
    const links = all(tree, 'a');
    assert.equal(links.length, 1);
    assert.equal(links[0].props.href, 'https://example.com/?a=1&b=2');
    assert.equal(links[0].props.target, '_blank');
    assert.equal(links[0].props.rel, 'noopener noreferrer');
    let stopped = false;
    links[0].props.onClick({ stopPropagation() { stopped = true; } });
    assert.equal(stopped, true);
    assert.equal(all(tree, 'img').length, 2);
    assert.ok(all(tree, 'img').every((image) => image.props.alt === '' && image.props['aria-hidden'] === 'true'));
});

test('button previews can disable interactive links while retaining decoded text and emoji', () => {
    const encoded = 'Меню -&gt; www.example.com 👍';
    const tree = MessageText.type({ text: encoded, links: false });
    assert.equal(all(tree, 'a').length, 0);
    assert.equal(textContent(tree), 'Меню -> www.example.com 👍');
    assert.equal(all(tree, 'img').length, 1);
});

test('decoded markup is escaped by React and never rendered as executable HTML', () => {
    const encoded = '&lt;img src=x onerror=alert(1)&gt; &lt;script&gt;alert(1)&lt;/script&gt; &#106;avascript:alert(1)';
    const tree = MessageText.type({ text: encoded });
    assert.equal(textContent(tree), '<img src=x onerror=alert(1)> <script>alert(1)</script> javascript:alert(1)');
    const markup = renderToStaticMarkup(React.createElement(MessageText, { text: encoded }));
    assert.match(markup, /&lt;script&gt;/);
    assert.doesNotMatch(markup, /<(?:img|script|a)\b/);
});
