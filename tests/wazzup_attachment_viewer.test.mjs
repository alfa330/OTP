import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const require = createRequire(import.meta.url);
const React = require('react');
const { build } = require('esbuild');
const cache = join(process.cwd(),'node_modules','.cache','otp-tests');
mkdirSync(cache,{recursive:true});
const output = join(cache,'WazzupAttachmentViewer.mjs');
await build({ entryPoints:[join(process.cwd(),'src/components/wazzup/ChatAttachmentViewer.jsx')],
    outfile:output,bundle:true,format:'esm',platform:'node',target:'node22',external:['lucide-react'],
    plugins:[{name:'attachment-ui-harness',setup(builder){
        builder.onResolve({filter:/^(react|react-dom|axios)$|\/ui\/ios$|\.\/pdfRuntime$/},({path}) => ({path,namespace:'fixture'}));
        builder.onLoad({filter:/.*/,namespace:'fixture'},({path}) => ({loader:'js',contents:
            path === 'react' ? `const h=()=>globalThis.__attachmentHarness;
                export const useState=(...v)=>h().useState(...v);
                export const useRef=(...v)=>h().useRef(...v);
                export const useEffect=(...v)=>h().useEffect(...v);
                export const useCallback=(...v)=>h().useCallback(...v);
                export default {Fragment:'fixture-fragment',createElement:(...v)=>h().createElement(...v)};`
            :path === 'react-dom' ? 'export const createPortal=(node)=>node;'
            :path === 'axios' ? 'export default {get:(...v)=>globalThis.__attachmentHarness.get(...v),post:(...v)=>globalThis.__attachmentHarness.post(...v)};'
            :path.endsWith('/ios') ? 'export const IosModal=({children})=>children;'
            :'export const getDocument=(...v)=>globalThis.__attachmentHarness.getDocument(...v); export class TextLayer {};',
        }));
    }}],
});
const {default:Viewer} = await import(pathToFileURL(output));
const defer=()=>{let resolve,reject; const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const tick=()=>new Promise((resolve)=>setImmediate(resolve));
const find=(node,predicate)=>{
    if (!node || typeof node!=='object') return null;
    if(predicate(node))return node;
    for(const child of React.Children.toArray(node.props?.children)){const result=find(child,predicate);if(result)return result;}
    return null;
};
const label=(tree,value)=>find(tree,(node)=>node.props?.['aria-label']===value);
const extractButton=(tree)=>find(tree,(node)=>node.type==='button'&&React.Children.toArray(node.props.children).includes(' Извлечь текст'));

function fixture({get,post,getDocument}={}){
    const previous={document:globalThis.document,Image:globalThis.Image,create:URL.createObjectURL,revoke:URL.revokeObjectURL};
    const created=[],revoked=[],requests=[],posts=[],slots=[];
    let index=0,effects=[],unmounted=false,lateWrites=0;
    const props={apiBaseUrl:'/fixture',headers:()=>({Authorization:'fixture'}),
        chat:{channelId:'channel',chatId:'chat'},message:{messageId:'message',contentUri:'https://store.wazzup24.com/a.pdf'},onClose:()=>{}};
    globalThis.document={body:{},activeElement:null,addEventListener(){},removeEventListener(){},
        createElement:(kind)=>kind==='canvas'?{width:0,height:0,getContext:()=>({fillRect(){},drawImage(){}}),toDataURL:()=> 'data:image/jpeg;base64,ZmFrZQ=='}:{} };
    globalThis.Image=class {naturalWidth=3000;naturalHeight=4000;async decode(){}};
    URL.createObjectURL=(blob)=>{const url=`blob:fixture-${created.length}`;created.push({blob,url});return url;};
    URL.revokeObjectURL=(url)=>revoked.push(url);
    const harness={createElement:React.createElement,
        get:(...args)=>{requests.push(args);return get?.(...args)||Promise.resolve({data:new Blob(['image'],{type:'image/png'})});},
        post:(...args)=>{posts.push(args);return post?.(...args)||Promise.resolve({data:{text:'Recognized'}});},
        getDocument:(...args)=>getDocument(...args),
        useState(initial){const i=index++;slots[i]??={value:typeof initial==='function'?initial():initial};return [slots[i].value,(value)=>{
            if(unmounted)lateWrites+=1;slots[i].value=typeof value==='function'?value(slots[i].value):value;
        }];},
        useRef(initial){const i=index++;slots[i]??={current:initial};return slots[i];},
        useCallback(fn,deps){const i=index++;if(!slots[i]||deps.some((v,k)=>!Object.is(v,slots[i].deps[k])))slots[i]={fn,deps};return slots[i].fn;},
        useEffect(fn,deps){const i=index++;const old=slots[i];if(old&&deps?.every((v,k)=>Object.is(v,old.deps[k])))return;
            slots[i]={deps};effects.push(()=>{old?.cleanup?.();slots[i].cleanup=fn();});},
        render(next){Object.assign(props,next);index=0;const result=Viewer(props);const pending=effects;effects=[];pending.forEach((fn)=>fn());return result;},
        unmount(){slots.forEach((slot)=>slot?.cleanup?.());unmounted=true;},
        restore(){if(!unmounted)this.unmount();globalThis.document=previous.document;globalThis.Image=previous.Image;
            URL.createObjectURL=previous.create;URL.revokeObjectURL=previous.revoke;delete globalThis.__attachmentHarness;},
        requests,posts,created,revoked,get lateWrites(){return lateWrites;},
    };
    globalThis.__attachmentHarness=harness;
    return harness;
}

test('closing before download completes aborts GET and ignores its late response',async()=>{
    const response=defer();const h=fixture({get:()=>response.promise});
    try{
        h.render();assert.equal(h.requests.length,1);h.unmount();
        assert.equal(h.requests[0][1].signal.aborted,true);
        response.resolve({data:new Blob(['late'],{type:'image/png'})});await tick();
        assert.equal(h.created.length,0);assert.equal(h.lateWrites,0);assert.equal(h.posts.length,0);
    }finally{h.restore();}
});

test('image opens without OCR; explicit extraction uses current IDs and same-tick clicks coalesce',async()=>{
    const response=defer();const h=fixture({post:()=>response.promise});
    try{
        h.render();await tick();let tree=h.render();
        assert.equal(h.posts.length,0);assert.equal(h.created.length,1);
        h.render({headers:()=>({Authorization:'refreshed'})});assert.equal(h.requests.length,1,'SSE/auth header rerender must not download again');
        const button=extractButton(tree);assert.ok(button&&!button.props.disabled);
        const first=button.props.onClick();await button.props.onClick();await tick();
        assert.equal(h.posts.length,1);
        const [url,payload,options]=h.posts[0];assert.match(url,/attachment-text$/);
        assert.deepEqual({...payload,imageDataUrl:null},{account:'op',channelId:'channel',chatId:'chat',messageId:'message',page:1,imageDataUrl:null});
        assert.match(payload.imageDataUrl,/^data:image\/jpeg;base64,/);assert.equal(options.timeout,65000);
        response.resolve({data:{text:'Extracted safely'}});await first;
        tree=h.render();assert.equal(label(tree,'Извлечённый текст').props.value,'Extracted safely');
        h.unmount();assert.deepEqual(h.revoked,['blob:fixture-0']);
    }finally{h.restore();}
});

test('closing during OCR cancels the POST and suppresses stale text',async()=>{
    const response=defer();const h=fixture({post:()=>response.promise});
    try{
        h.render();await tick();const work=extractButton(h.render()).props.onClick();await tick();
        h.unmount();assert.equal(h.posts[0][2].signal.aborted,true);
        response.resolve({data:{text:'Must not appear'}});await work;
        assert.equal(h.lateWrites,0);
    }finally{h.restore();}
});

test('native PDF text stays local, switching page aborts OCR, and loading task is destroyed',async()=>{
    const response=defer();let destroyed=0;
    const pdf={numPages:2};
    const h=fixture({get:()=>Promise.resolve({data:new Blob(['%PDF'],{type:'application/pdf'})}),
        post:()=>response.promise,getDocument:()=>({promise:Promise.resolve(pdf),destroy:async()=>{destroyed+=1;}})});
    try{
        h.render();await tick();await tick();let tree=h.render();
        const page=find(tree,(node)=>node.props?.document===pdf);
        page.props.onReady({text:'Native text',page:{getViewport:()=>({width:600,height:800}),render:()=>({promise:Promise.resolve(),cancel(){}})}});
        tree=h.render();await extractButton(tree).props.onClick();tree=h.render();
        assert.equal(label(tree,'Извлечённый текст').props.value,'Native text');assert.equal(h.posts.length,0);
        const scan=find(tree,(node)=>node.type==='button'&&React.Children.toArray(node.props.children).includes(' Распознать скан'));
        const work=scan.props.onClick();await tick();assert.equal(h.posts.length,1);
        label(h.render(),'Следующая страница').props.onClick();assert.equal(h.posts[0][2].signal.aborted,true);
        response.resolve({data:{text:'Stale page one OCR'}});await work;
        tree=h.render();assert.equal(label(tree,'Извлечённый текст'),null);
        assert.equal(find(tree,(node)=>node.props?.document===pdf).props.number,2);
        h.unmount();assert.equal(destroyed,1);assert.deepEqual(h.revoked,['blob:fixture-0']);
    }finally{h.restore();}
});
