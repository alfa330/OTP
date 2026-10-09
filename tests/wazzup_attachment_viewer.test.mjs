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
        builder.onResolve({filter:/^(react|react-dom|axios)$|\/ui\/ios$|\.\/pdfRuntime$|\.\/useAttachmentPip$/},({path}) => ({path,namespace:'fixture'}));
        builder.onLoad({filter:/.*/,namespace:'fixture'},({path}) => ({loader:'js',contents:
            path === 'react' ? `const h=()=>globalThis.__attachmentHarness;
                export const useState=(...v)=>h().useState(...v);
                export const useRef=(...v)=>h().useRef(...v);
                export const useEffect=(...v)=>h().useEffect(...v);
                export const useLayoutEffect=(...v)=>h().useEffect(...v);
                export const useCallback=(...v)=>h().useCallback(...v);
                export default {Fragment:'fixture-fragment',createElement:(...v)=>h().createElement(...v)};`
            :path === 'react-dom' ? 'export const createPortal=(node,target)=>{globalThis.__attachmentHarness.portalTarget=target;return node;};'
            :path === 'axios' ? 'export default {get:(...v)=>globalThis.__attachmentHarness.get(...v),post:(...v)=>globalThis.__attachmentHarness.post(...v)};'
            :path.endsWith('/ios') ? 'export const IosModal=({children})=>children;'
            :path.endsWith('/useAttachmentPip') ? 'export default ()=>globalThis.__attachmentHarness.pip;'
            :'export const getDocument=(...v)=>globalThis.__attachmentHarness.getDocument(...v); export class TextLayer {};',
        }));
    }}],
});
const {default:Viewer} = await import(pathToFileURL(output));
const {createAttachmentCache} = await import('../src/components/wazzup/attachmentCache.js');
const defer=()=>{let resolve,reject; const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
const tick=()=>new Promise((resolve)=>setImmediate(resolve));
const find=(node,predicate)=>{
    if (!node || typeof node!=='object') return null;
    if(predicate(node))return node;
    if (node.type?.name === 'ChatAttachmentStrip') return find(node.type(node.props), predicate);
    for(const child of React.Children.toArray(node.props?.children)){const result=find(child,predicate);if(result)return result;}
    return null;
};
const label=(tree,value)=>find(tree,(node)=>node.props?.['aria-label']===value);
const extractButton=(tree)=>find(tree,(node)=>node.type==='button'&&React.Children.toArray(node.props.children).includes(' Извлечь текст'));

function fixture({get,post,getDocument,cache}={}){
    const previous={document:globalThis.document,Image:globalThis.Image,create:URL.createObjectURL,revoke:URL.revokeObjectURL};
    const created=[],revoked=[],requests=[],posts=[],slots=[],listeners=new Map(),videos=[];
    let videoKey;
    let index=0,effects=[],unmounted=false,lateWrites=0;
    const props={apiBaseUrl:'/fixture',headers:()=>({Authorization:'fixture'}),
        chat:{channelId:'channel',chatId:'chat'},message:{messageId:'message',contentUri:'https://store.wazzup24.com/a.pdf'},onClose:()=>{},
        cache:cache||createAttachmentCache()};
    globalThis.document={body:{appendChild(node){node.parentNode=this;node.ownerDocument=globalThis.document;node.isConnected=true;}},activeElement:null,addEventListener(name,handler){listeners.set(name,handler);},removeEventListener(name){listeners.delete(name);},
        createElement:(kind)=>kind==='canvas'?{width:0,height:0,getContext:()=>({fillRect(){},drawImage(){}}),toDataURL:()=> 'data:image/jpeg;base64,ZmFrZQ=='}:{style:{},querySelectorAll(){return [];},remove(){this.parentNode=null;this.isConnected=false;}} };
    globalThis.Image=class {naturalWidth=3000;naturalHeight=4000;async decode(){}};
    URL.createObjectURL=(blob)=>{const url=`blob:fixture-${created.length}`;created.push({blob,url});return url;};
    URL.revokeObjectURL=(url)=>revoked.push(url);
    const harness={createElement:React.createElement,pip:{supported:false,pipWindow:null,opening:false,error:''},
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
        render(next){Object.assign(props,next);index=0;const result=Viewer(props);
            const video=find(result,(node)=>node.type==='video');
            if(video){
                if(videoKey!==video.key){videoKey=video.key;videos.push({src:video.props.src,pauseCalls:0,loadCalls:0,
                    pause(){this.pauseCalls++;},load(){this.loadCalls++;},getAttribute(name){return this[name];},removeAttribute(name){delete this[name];}});}
                video.ref.current=videos.at(-1);
            }
            const pending=effects;effects=[];pending.forEach((fn)=>fn());return result;},
        unmount(){slots.forEach((slot)=>slot?.cleanup?.());unmounted=true;},
        restore(){if(!unmounted)this.unmount();globalThis.document=previous.document;globalThis.Image=previous.Image;
            URL.createObjectURL=previous.create;URL.revokeObjectURL=previous.revoke;delete globalThis.__attachmentHarness;},
        requests,posts,created,revoked,videos,dispatch:(event)=>listeners.get('keydown')?.(event),get lateWrites(){return lateWrites;},
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

test('moving the viewer keeps its host, zoom and OCR while keyboard events follow its window',async()=>{
    let closed=0;
    const detached=[];
    const h=fixture();
    const pipKeys=new Map();
    const pipDocument={body:{appendChild(node){node.parentNode=this;node.ownerDocument=pipDocument;node.isConnected=true;}},
        addEventListener(name,fn){pipKeys.set(name,fn);},removeEventListener(name){pipKeys.delete(name);}};
    try{
        h.render({onClose:()=>closed++,onDetachedChange:value=>detached.push(value)});await tick();
        const host=h.portalTarget;
        label(h.render(),'Увеличить').props.onClick();
        await extractButton(h.render()).props.onClick();
        const posts=h.posts.length,requests=h.requests.length;
        h.pip={...h.pip,pipWindow:{document:pipDocument}};
        let tree=h.render();
        assert.equal(tree.props.embedded,true);
        assert.equal(h.portalTarget,host);assert.equal(host.ownerDocument,pipDocument);
        assert.equal(label(tree,'Извлечённый текст').props.value,'Recognized');
        assert.equal(find(tree,node=>node.type==='img').props.style.width,'125%');
        h.dispatch({key:'Escape',stopPropagation(){}});assert.equal(closed,0,'main chat Escape must not close PiP');
        pipKeys.get('keydown')({key:'Escape',stopPropagation(){}});assert.equal(closed,1);
        h.pip={...h.pip,pipWindow:null};tree=h.render();
        assert.equal(h.portalTarget,host);assert.equal(host.ownerDocument,document);
        assert.equal(pipKeys.size,0);assert.equal(tree.props.embedded,false);
        assert.equal(label(tree,'Извлечённый текст').props.value,'Recognized');
        assert.equal(h.posts.length,posts);assert.equal(h.requests.length,requests);
        assert.deepEqual(detached,[false,true,false]);
    }finally{h.restore();}
});

test('detaching during recognition preserves the request and its result',async()=>{
    const response=defer();const h=fixture({post:()=>response.promise});
    try{
        h.render();await tick();
        const work=extractButton(h.render()).props.onClick();await tick();
        h.pip={...h.pip,pipWindow:{document:{body:{appendChild(){}},addEventListener(){},removeEventListener(){}}}};
        h.render();assert.equal(h.posts[0][2].signal.aborted,false);
        response.resolve({data:{text:'Completed inside PiP'}});await work;
        assert.equal(label(h.render(),'Извлечённый текст').props.value,'Completed inside PiP');
        assert.equal(h.posts.length,1);assert.equal(h.requests.length,1);
    }finally{h.restore();}
});

test('PiP copy and download use the attachment document rather than the opener',async()=>{
    let selected=0,clicked=0,removed=0;
    const commands=[],anchors=[];
    const pipDocument={body:{appendChild(node){anchors.push(node);}},addEventListener(){},removeEventListener(){},
        execCommand(command){commands.push(command);return true;},
        createElement(kind){assert.equal(kind,'a');return {click(){clicked++;},remove(){removed++;}};}};
    const h=fixture();
    try{
        h.render();await tick();await extractButton(h.render()).props.onClick();
        h.pip={...h.pip,pipWindow:{document:pipDocument}};
        const tree=h.render();
        find(tree,node=>node.props?.tabIndex===-1).ref.current={ownerDocument:pipDocument,focus(){}};
        label(tree,'Извлечённый текст').ref.current={focus(){},select(){selected++;}};
        await label(tree,'Копировать текст').props.onClick();
        assert.deepEqual(commands,['copy']);assert.equal(selected,1);
        assert.equal(label(h.render(),'Копировать текст').props.title,'Скопировано');
        label(tree,'Скачать файл').props.onClick();
        assert.equal(clicked,1);assert.equal(removed,1);
        assert.equal(anchors.at(-1).href,'blob:fixture-0');
        assert.equal(anchors.at(-1).download,'a.pdf');
    }finally{h.restore();}
});

test('video is not paused, reloaded or replaced when its host changes documents',async()=>{
    const h=fixture();
    try{
        h.render({message:{messageId:'movie',type:'video',contentUri:'https://example.invalid/video.mp4'}});await tick();
        const video=h.videos[0];video.currentTime=12;
        h.pip={...h.pip,pipWindow:{document:{body:{appendChild(){}},addEventListener(){},removeEventListener(){}}}};
        h.render();h.pip={...h.pip,pipWindow:null};h.render();
        assert.equal(h.videos.length,1);assert.equal(video.pauseCalls,0);assert.equal(video.loadCalls,0);assert.equal(video.currentTime,12);
    }finally{h.restore();}
});

test('video streams directly with native controls and has no proxy download, OCR or object URL',async()=>{
    let closed=0;
    const message={messageId:'movie',type:'video',contentUri:'https://example.invalid/video.mp4'};
    const h=fixture();
    try{
        let tree=h.render({message,onClose:()=>{closed++;}});await tick();tree=h.render();
        const video=find(tree,(node)=>node.type==='video');
        assert.equal(video.props.src,message.contentUri);assert.equal(video.props.controls,true);
        assert.equal(video.props.playsInline,true);assert.equal(video.props.autoPlay,true);
        assert.equal(extractButton(tree),null);assert.equal(label(tree,'Увеличить'),null);
        assert.equal(label(tree,'Скачать оригинал').props.href,message.contentUri);
        assert.equal(h.requests.length,0);assert.equal(h.posts.length,0);assert.equal(h.created.length,0);
        const media=h.videos[0];
        // IosModal routes its backdrop, close button and mobile back action here.
        tree.props.onClose();assert.equal(closed,1);assert.equal(media.pauseCalls,1);
        h.dispatch({key:'Escape',stopPropagation(){}});assert.equal(closed,2);assert.equal(media.pauseCalls,2);
        h.unmount();assert.equal(media.pauseCalls,3);assert.equal(media.src,undefined);assert.equal(media.loadCalls,1);
        video.props.onError({currentTarget:media});assert.equal(h.lateWrites,0);
    }finally{h.restore();}
});

test('video navigation stops the old stream, preserves native arrow controls and ignores old errors',async()=>{
    const first={messageId:'v1',type:'video',contentUri:'https://example.invalid/one.mp4'};
    const second={messageId:'v2',type:'video',contentUri:'https://example.invalid/two.mp4'};
    let selected;
    const h=fixture();
    try{
        let tree=h.render({message:first,items:[first,second],onSelect:(value)=>{selected=value;}});await tick();tree=h.render();
        const previous=find(tree,(node)=>node.type==='video');const oldMedia=h.videos[0];
        let prevented=false;
        h.dispatch({key:'ArrowRight',target:{closest:(selector)=>selector.includes('video')?{}:null},preventDefault(){prevented=true;}});
        assert.equal(prevented,false);assert.equal(selected,undefined);
        label(tree,'Следующее вложение').props.onClick();assert.equal(oldMedia.pauseCalls,1);
        tree=h.render({message:selected});await tick();tree=h.render();
        assert.equal(oldMedia.src,undefined);assert.equal(oldMedia.loadCalls,1);
        assert.equal(h.videos.length,2);assert.equal(h.videos[1].src,second.contentUri);
        previous.props.onError({currentTarget:oldMedia});assert.equal(find(h.render(),(node)=>node.props?.role==='alert'),null);
        assert.equal(h.requests.length,0);assert.equal(h.posts.length,0);
    }finally{h.restore();}
});

test('video failure offers retry/original and replacement releases the failed media',async()=>{
    const message={messageId:'video',type:'video',contentUri:'https://example.invalid/movie.mp4'};
    const h=fixture();
    try{
        let tree=h.render({message});await tick();tree=h.render();
        const oldVideo=find(tree,(node)=>node.type==='video'), oldMedia=h.videos[0];
        oldVideo.props.onError({currentTarget:oldMedia});tree=h.render();
        assert.ok(find(tree,(node)=>node.props?.role==='alert'));
        find(tree,(node)=>node.type==='button'&&node.props.children==='Повторить').props.onClick();
        h.render();await tick();tree=h.render();
        assert.equal(oldMedia.src,undefined);assert.equal(h.videos.length,2);
        oldVideo.props.onError({currentTarget:oldMedia});assert.equal(find(h.render(),(node)=>node.props?.role==='alert'),null);
        assert.equal(h.requests.length,0);assert.equal(h.posts.length,0);
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

test('a conversation photo is visible and zoomable while its OCR copy is still downloading',async()=>{
    const response=defer();const h=fixture({get:()=>response.promise});
    const message={messageId:'visible-photo',type:'image',contentUri:'https://example.invalid/already-visible.jpg'};
    try{
        let tree=h.render({message});tree=h.render();
        assert.equal(find(tree,node=>node.type==='img').props.src,message.contentUri);
        assert.equal(find(tree,node=>node.props?.role==='status'),null);
        assert.equal(extractButton(tree).props.disabled,true);
        label(tree,'Увеличить').props.onClick();tree=h.render();
        assert.equal(find(tree,node=>node.type==='img').props.style.width,'125%');
        response.resolve({data:new Blob(['ready'],{type:'image/jpeg'})});await tick();tree=h.render();
        assert.equal(find(tree,node=>node.type==='img').props.src,message.contentUri,'ready OCR bytes must not flash or replace the displayed image');
        assert.equal(find(tree,node=>node.type==='img').props.style.width,'125%');
        assert.equal(extractButton(tree).props.disabled,false);
        assert.equal(h.requests.length,1);assert.equal(h.posts.length,0);
    }finally{h.restore();}
});

test('an unavailable direct preview falls back to the authenticated downloaded image',async()=>{
    const response=defer();const h=fixture({get:()=>response.promise});
    try{
        let tree=h.render({message:{messageId:'photo',type:'image',contentUri:'https://example.invalid/blocked.jpg'}});
        find(tree,node=>node.type==='img').props.onError();tree=h.render();
        assert.equal(find(tree,node=>node.type==='img'),null);
        response.resolve({data:new Blob(['copy'],{type:'image/png'})});await tick();tree=h.render();
        assert.equal(find(tree,node=>node.type==='img').props.src,'blob:fixture-0');
        assert.equal(find(tree,node=>node.props?.role==='alert'),null);
    }finally{h.restore();}
});

test('text reading mode opens after extraction and switches without new OCR or download',async()=>{
    const h=fixture();
    const button=(tree,text)=>find(tree,node=>node.type==='button'&&node.props.children===text);
    try{
        h.render();await tick();await extractButton(h.render()).props.onClick();
        let tree=h.render();
        assert.equal(button(tree,'Текст').props['aria-pressed'],true);
        assert.equal(find(tree,node=>node.type?.name==='ChatAttachmentStrip'),null);
        const text=label(tree,'Извлечённый текст').props.value;
        button(tree,'Файл').props.onClick();tree=h.render();
        assert.equal(button(tree,'Файл').props['aria-pressed'],true);
        button(tree,'Текст').props.onClick();tree=h.render();
        label(tree,'Развернуть текст').props.onClick();tree=h.render();
        assert.ok(label(tree,'Показать файл рядом с текстом'));
        label(tree,'Показать файл рядом с текстом').props.onClick();tree=h.render();
        assert.equal(label(tree,'Извлечённый текст').props.value,text);
        assert.equal(h.requests.length,1);assert.equal(h.posts.length,1);
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

const media = [
    {messageId:'photo-one',type:'image',fileName:'one.jpg',contentUri:'https://store.wazzup24.com/one.jpg'},
    {messageId:'document-two',type:'document',fileName:'two.pdf',contentUri:'https://store.wazzup24.com/two.pdf'},
    {messageId:'photo-three',type:'image',fileName:'three.png',contentUri:'https://store.wazzup24.com/three.png'},
];

test('gallery downloads only the selected item; thumbnails are lazy and navigation has boundaries',async()=>{
    const selected=[];const h=fixture();
    try{
        h.render({message:media[0],items:media,onSelect:(message)=>selected.push(message)});await tick();let tree=h.render();
        assert.equal(h.requests.length,1);assert.equal(h.requests[0][1].params.messageId,'photo-one');
        assert.equal(label(tree,'Предыдущее вложение').props.disabled,true);
        assert.equal(label(tree,'Следующее вложение').props.disabled,false);
        const first=label(tree,'Вложение 1: one.jpg');assert.equal(first.props['aria-pressed'],true);
        assert.equal(find(first,(node)=>node.type==='img').props.loading,'lazy');
        assert.equal(find(label(tree,'Вложение 2: two.pdf'),(node)=>node.type==='img'),null);
        label(tree,'Следующее вложение').props.onClick();label(tree,'Следующее вложение').props.onClick();
        assert.deepEqual(selected.map((m)=>m.messageId),['document-two','photo-three'],'same-tick navigation uses most recently requested selection');
        label(tree,'Следующее вложение').props.onClick();assert.equal(selected.length,2,'no wrap beyond last item');
        h.render({message:selected.at(-1)});await tick();tree=h.render();
        assert.equal(h.requests.length,2);assert.equal(h.requests[1][1].params.messageId,'photo-three');
        assert.equal(label(tree,'Следующее вложение').props.disabled,true);
        assert.equal(label(tree,'Предыдущее вложение').props.disabled,false);
        assert.deepEqual(h.revoked,['blob:fixture-0']);
        h.render({items:[media[2]]});assert.equal(label(h.render(),'Вложения из этой группы'),null);
    }finally{h.restore();}
});

test('rapid selection aborts old download and ignores its later response',async()=>{
    const first=defer(),second=defer();const h=fixture({get:(_url,options)=>options.params.messageId===media[0].messageId?first.promise:second.promise});
    let selected;
    try{
        h.render({message:media[0],items:media,onSelect:(message)=>{selected=message;}});
        label(h.render(),'Вложение 3: three.png').props.onClick();assert.equal(h.requests[0][1].signal.aborted,true);
        h.render({message:selected});
        second.resolve({data:new Blob(['current'],{type:'image/png'})});await tick();
        first.resolve({data:new Blob(['stale'],{type:'image/jpeg'})});await tick();
        assert.equal(h.created.length,1);assert.equal(await h.created[0].blob.text(),'current');
        assert.equal(find(h.render(),(node)=>node.type==='img'&&node.props.alt==='Вложение из сообщения').props.src,media[2].contentUri);
    }finally{h.restore();}
});

test('same-tick next then previous restarts aborted download even when final selection is unchanged',async()=>{
    const pending=[defer(),defer()];let calls=0;let selected;
    const h=fixture({get:()=>pending[calls++].promise});
    try{
        h.render({message:media[0],items:media,onSelect:(message)=>{selected=message;}});
        const tree=h.render();label(tree,'Следующее вложение').props.onClick();
        label(tree,'Предыдущее вложение').props.onClick();
        assert.equal(selected.messageId,'photo-one');assert.equal(h.requests[0][1].signal.aborted,true);
        h.render({message:selected});assert.equal(h.requests.length,2);
        pending[1].resolve({data:new Blob(['restarted'],{type:'image/png'})});await tick();
        pending[0].resolve({data:new Blob(['aborted'],{type:'image/png'})});await tick();
        assert.equal(h.created.length,1);assert.equal(await h.created[0].blob.text(),'restarted');
        assert.ok(find(h.render(),(node)=>node.type==='img'&&node.props.alt==='Вложение из сообщения'));
    }finally{h.restore();}
});

test('switching during OCR aborts it and clears prior text, zoom, and progress',async()=>{
    const response=defer();const h=fixture({post:()=>response.promise});let selected;
    try{
        h.render({message:media[0],items:media,onSelect:(message)=>{selected=message;}});await tick();let tree=h.render();
        label(tree,'Увеличить').props.onClick();tree=h.render();
        const work=extractButton(tree).props.onClick();await tick();assert.equal(h.posts.length,1);
        label(h.render(),'Вложение 3: three.png').props.onClick();assert.equal(h.posts[0][2].signal.aborted,true);
        tree=h.render({message:selected});assert.equal(label(tree,'Извлечённый текст'),null);
        await tick();response.resolve({data:{text:'Text from previous file must not appear'}});await work;
        tree=h.render();assert.equal(label(tree,'Извлечённый текст'),null);
        assert.equal(find(tree,(node)=>node.type==='img'&&node.props.alt==='Вложение из сообщения').props.style.width,'100%');
        assert.equal(extractButton(tree).props.disabled,false,'OCR button resets on new image');
    }finally{h.restore();}
});

test('late clipboard completion from previous file does not mark the next file copied',async()=>{
    const response=defer();const previous=Object.getOwnPropertyDescriptor(navigator,'clipboard');
    Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:()=>response.promise}});
    const h=fixture();let selected;
    try{
        h.render({message:media[0],items:media,onSelect:(message)=>{selected=message;}});await tick();
        await extractButton(h.render()).props.onClick();
        const work=label(h.render(),'Копировать текст').props.onClick();
        label(h.render(),'Вложение 3: three.png').props.onClick();h.render({message:selected});await tick();
        await extractButton(h.render()).props.onClick();
        response.resolve();await work;
        assert.equal(label(h.render(),'Копировать текст').props.title,'Копировать текст');
    }finally{
        h.restore();
        if(previous)Object.defineProperty(navigator,'clipboard',previous);else delete navigator.clipboard;
    }
});

test('known office documents show original links without a GET; unknown names are MIME-detected',async()=>{
    const office={messageId:'office',type:'document',fileName:'letter.docx',contentUri:'https://store.wazzup24.com/letter.docx'};
    const h=fixture();
    try{
        h.render({message:office,items:[office,...media],onSelect:()=>{}});await tick();let tree=h.render();
        assert.equal(h.requests.length,0);assert.equal(extractButton(tree),null);
        assert.equal(label(tree,'Скачать оригинал').props.href,office.contentUri);
        assert.equal(find(tree,(node)=>node.type==='a'&&node.props.children==='Открыть оригинал').props.href,office.contentUri);
        h.render({message:{messageId:'opaque',type:'document',contentUri:'https://store.wazzup24.com/opaque-id'}});await tick();
        assert.equal(h.requests.length,1);assert.equal(h.requests[0][1].params.messageId,'opaque');
    }finally{h.restore();}
});

test('keyboard gallery arrows work on thumbnails but leave text, modified keys and PDF page controls alone',async()=>{
    const selected=[];const h=fixture();
    const event=(target,extra={})=>({key:'ArrowRight',target,preventDefault(){this.prevented=true;},...extra});
    try{
        h.render({message:media[0],items:media,onSelect:(message)=>selected.push(message)});await tick();
        for(const target of [
            {closest:(selector)=>selector.includes('input,')?{}:null},
            {closest:(selector)=>selector==='button, a'?{}:null},
            {isContentEditable:true},
        ]){const e=event(target);h.dispatch(e);assert.equal(e.prevented,undefined);}
        h.dispatch(event({}, {ctrlKey:true}));assert.equal(selected.length,0);
        const thumb=event({closest:(selector)=>['button, a','[data-attachment-navigation]'].includes(selector)?{}:null});
        h.dispatch(thumb);assert.equal(thumb.prevented,true);assert.equal(selected[0].messageId,'document-two');
        const blank=event({});h.dispatch(blank);assert.equal(selected[1].messageId,'photo-three');
    }finally{h.restore();}
});

test('PDF pages and attachment index are independent, and switching destroys pending PDF work',async()=>{
    const pending=defer();let destroyed=0;let selected;
    const h=fixture({get:(_url,options)=>Promise.resolve({data:new Blob(['fixture'],{type:options.params.messageId===media[1].messageId?'application/pdf':'image/png'})}),
        getDocument:()=>({promise:pending.promise,destroy:async()=>{destroyed+=1;}})});
    try{
        h.render({message:media[1],items:media,onSelect:(message)=>{selected=message;}});await tick();await tick();
        label(h.render(),'Следующее вложение').props.onClick();h.render({message:selected});await tick();
        assert.equal(destroyed,1);pending.resolve({numPages:7});await tick();
        let tree=h.render();assert.equal(label(tree,'Следующая страница'),null);assert.equal(label(tree,'Следующее вложение').props.disabled,true);
        assert.deepEqual(h.revoked,['blob:fixture-0']);
        h.render({message:media[1]});await tick();await tick();tree=h.render();
        assert.equal(label(tree,'Следующая страница').props.disabled,false);
        selected=null;label(tree,'Следующая страница').props.onClick();tree=h.render();
        assert.equal(selected,null,'PDF page controls must not select another attachment');
        assert.equal(find(tree,(node)=>node.props?.document?.numPages===7).props.number,2);
        assert.equal(label(tree,'Вложение 2: two.pdf').props['aria-pressed'],true);
    }finally{h.restore();}
});

test('reopening an attachment reuses the downloaded file and the recognized text',async()=>{
    const cache=createAttachmentCache();
    let h=fixture({cache});
    try{
        h.render();await tick();
        await extractButton(h.render()).props.onClick();
        assert.equal(label(h.render(),'Извлечённый текст').props.value,'Recognized');
        assert.equal(h.requests.length,1);assert.equal(h.posts.length,1);
    }finally{h.restore();}
    // The viewer was closed. Opening the same file again is a new component.
    h=fixture({cache,get:()=>{throw new Error('must not download again');},
        post:()=>{throw new Error('must not recognize again');}});
    try{
        let tree=h.render();
        assert.equal(find(tree,(node)=>node.props?.role==='status'),null,'a cached image shows no progress indicator');
        await tick();tree=h.render();
        assert.equal(find(tree,(node)=>node.props?.role==='status'),null);
        assert.equal(label(tree,'Извлечённый текст').props.value,'Recognized','stored recognition is shown without a request');
        assert.equal(h.requests.length,0);assert.equal(h.posts.length,0);
        assert.ok(find(tree,(node)=>node.type==='img'&&node.props.src==='blob:fixture-0'));
        h.unmount();assert.deepEqual(h.revoked,['blob:fixture-0'],'the object URL is released, the cached file is not');
        assert.equal(cache.stats().mediaItems,1);
    }finally{h.restore();}
});

test('asking for recognition that is already on screen forces a new attempt',async()=>{
    const answers=['First attempt','Second attempt'];
    const h=fixture({post:()=>Promise.resolve({data:{text:answers.shift()}})});
    try{
        h.render();await tick();
        await extractButton(h.render()).props.onClick();
        assert.equal(h.posts[0][1].refresh,undefined,'the first request may be answered from the server cache');
        await extractButton(h.render()).props.onClick();
        assert.equal(h.posts.length,2);assert.equal(h.posts[1][1].refresh,true);
        assert.equal(label(h.render(),'Извлечённый текст').props.value,'Second attempt');
    }finally{h.restore();}
});

test('switching back to an attachment in the gallery does not download it twice',async()=>{
    const h=fixture();
    try{
        h.render({message:media[0],items:media,onSelect:(message)=>h.render({message})});await tick();
        assert.equal(h.requests.length,1);
        label(h.render(),'Следующее вложение').props.onClick();await tick();await tick();
        const afterSecond=h.requests.length;
        label(h.render(),'Предыдущее вложение').props.onClick();await tick();
        assert.equal(h.requests.length,afterSecond,'the first photo comes from the cache');
        assert.ok(find(h.render(),(node)=>node.type==='img'));
    }finally{h.restore();}
});

test('a file the viewer cannot show is not kept',async()=>{
    const cache=createAttachmentCache();
    const h=fixture({cache,get:()=>Promise.resolve({data:new Blob(['PK'],{type:'application/zip'})})});
    try{
        h.render();await tick();await tick();
        assert.ok(find(h.render(),(node)=>node.props?.role==='alert'));
        assert.equal(cache.stats().mediaItems,0);
    }finally{h.restore();}
});

test('a failed PDF parse is downloaded again when the operator retries',async()=>{
    let parses=0;
    const cache=createAttachmentCache();
    const pdf={numPages:1};
    const h=fixture({cache,get:()=>Promise.resolve({data:new Blob(['%PDF'],{type:'application/pdf'})}),
        getDocument:()=>({promise:++parses===1?Promise.reject(new Error('Invalid PDF')):Promise.resolve(pdf),destroy:async()=>{}})});
    try{
        h.render();await tick();await tick();
        const failed=h.render();
        assert.ok(find(failed,(node)=>node.props?.role==='alert'));
        assert.equal(cache.stats().mediaItems,0,'an unreadable download must not survive in the cache');
        const retry=find(failed,(node)=>node.type==='button'&&node.props.children==='Повторить');
        assert.ok(retry);retry.props.onClick();h.render();await tick();await tick();
        assert.equal(h.requests.length,2);
        assert.ok(find(h.render(),(node)=>node.props?.document===pdf));
        assert.equal(cache.stats().mediaItems,1);
    }finally{h.restore();}
});

test('a changed attachment URL or API cannot reuse the previous bytes or OCR',async()=>{
    const h=fixture();
    try{
        h.render();await tick();
        await extractButton(h.render()).props.onClick();
        assert.equal(label(h.render(),'Извлечённый текст').props.value,'Recognized');
        h.render({message:{messageId:'message',contentUri:'https://store.wazzup24.com/replacement.pdf'}});
        await tick();
        assert.equal(h.requests.length,2);
        assert.equal(label(h.render(),'Извлечённый текст'),null);
        await extractButton(h.render()).props.onClick();
        h.render({apiBaseUrl:'/other-api'});await tick();
        assert.equal(h.requests.length,3);
        assert.equal(label(h.render(),'Извлечённый текст'),null);
        assert.match(h.requests[2][0],/^\/other-api\//);
    }finally{h.restore();}
});

test('a busy download slot is retried quietly before an error is shown',async()=>{
    let calls=0;
    const busy=Object.assign(new Error('busy'),{response:{status:429,data:{error:'Загрузка занята'}}});
    const h=fixture({get:()=>{calls+=1;return calls===1?Promise.reject(busy):Promise.resolve({data:new Blob(['image'],{type:'image/png'})});}});
    try{
        h.render();await tick();
        assert.equal(find(h.render(),(node)=>node.props?.role==='alert'),null);
        await new Promise((resolve)=>setTimeout(resolve,750));await tick();
        const tree=h.render();
        assert.equal(calls,2);assert.equal(find(tree,(node)=>node.props?.role==='alert'),null);
        assert.ok(find(tree,(node)=>node.type==='img'));
    }finally{h.restore();}
});

test('a download cancelled during the quiet retry is not repeated',async()=>{
    let calls=0;
    const busy=Object.assign(new Error('busy'),{response:{status:429,data:{error:'Загрузка занята'}}});
    const h=fixture({get:()=>{calls+=1;return Promise.reject(busy);}});
    try{
        h.render();await tick();h.unmount();
        await new Promise((resolve)=>setTimeout(resolve,750));
        assert.equal(calls,1);assert.equal(h.lateWrites,0);
    }finally{h.restore();}
});
