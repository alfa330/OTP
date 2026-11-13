/* Презентация PowerPoint (.pptx) → слайды для чтения.

   Точную вёрстку слайда (координаты, темы, анимации) не воспроизводим: окно
   показывает содержание каждого слайда — заголовок, пункты, таблицы и
   картинки — в порядке показа. Этого хватает, чтобы прочесть презентацию,
   не скачивая её. */
import { attr, child, childrenOf, elements, findFirst, relId, textContent } from './xmlTree.js';
import { loadImages, mainPartPath, openPackage, PreviewError, readRels, readXml } from './ooxml.js';

function paragraphText(p) {
    let out = '';
    elements(p).forEach((item) => {
        if (item.local === 'r' || item.local === 'fld') out += textContent(child(item, 't'));
        else if (item.local === 'br') out += '\n';
    });
    return out;
}

function readTextBody(txBody, defaultBullets) {
    return childrenOf(txBody, 'p').map((p) => {
        const pPr = child(p, 'pPr');
        let bullet = defaultBullets;
        if (child(pPr, 'buNone')) bullet = false;
        if (child(pPr, 'buChar') || child(pPr, 'buAutoNum')) bullet = true;
        return { text: paragraphText(p), level: Number(attr(pPr, 'lvl') || 0) || 0, bullet };
    }).filter((paragraph) => paragraph.text.trim());
}

function readShapes(tree, slide, rels, images) {
    elements(tree).forEach((node) => {
        switch (node.local) {
        case 'sp': {
            const ph = findFirst(child(node, 'nvSpPr'), 'ph');
            const type = ph ? attr(ph, 'type') || 'body' : '';
            const paragraphs = readTextBody(child(node, 'txBody'), ['body', 'obj'].includes(type));
            if (!paragraphs.length) return;
            if ((type === 'title' || type === 'ctrTitle') && !slide.title) {
                slide.title = paragraphs.map((item) => item.text).join(' ');
            } else if (!['sldNum', 'dt', 'ftr'].includes(type)) {
                slide.blocks.push({ type: 'text', subtitle: type === 'subTitle', paragraphs });
            }
            break;
        }
        case 'grpSp': readShapes(node, slide, rels, images); break;
        case 'graphicFrame': {
            const table = findFirst(node, 'tbl');
            if (table) {
                slide.blocks.push({
                    type: 'table',
                    rows: childrenOf(table, 'tr').map((tr) => childrenOf(tr, 'tc')
                        .filter((tc) => !attr(tc, 'hMerge') && !attr(tc, 'vMerge'))
                        .map((tc) => childrenOf(child(tc, 'txBody'), 'p').map(paragraphText).join('\n'))),
                });
            }
            break;
        }
        case 'pic': {
            const blip = findFirst(node, 'blip');
            const rel = rels.get(attr(blip, 'embed'));
            if (rel && !rel.external) {
                images.add(rel.target);
                slide.blocks.push({ type: 'image', path: rel.target });
            }
            break;
        }
        case 'AlternateContent': {
            const branch = child(node, 'Choice') || child(node, 'Fallback');
            if (branch) readShapes(branch, slide, rels, images);
            break;
        }
        default: break;
        }
    });
}

export function slidesToText(slides) {
    return slides.map((slide) => {
        const lines = [`Слайд ${slide.number}${slide.title ? `. ${slide.title}` : ''}`];
        slide.blocks.forEach((block) => {
            if (block.type === 'text') {
                block.paragraphs.forEach((item) => lines.push(`${'  '.repeat(item.level)}${item.bullet ? '• ' : ''}${item.text}`));
            } else if (block.type === 'table') {
                block.rows.forEach((row) => lines.push(row.join('\t')));
            }
        });
        return lines.join('\n');
    }).join('\n\n');
}

export async function parsePptx(bytes) {
    const zip = await openPackage(bytes);
    const presentationPath = await mainPartPath(zip, 'ppt/presentation.xml');
    const presentation = await readXml(zip, presentationPath);
    const list = findFirst(presentation, 'sldIdLst');
    if (!presentation || !list) throw new PreviewError('В презентации нет слайдов.');
    const rels = await readRels(zip, presentationPath);
    const images = new Set();
    const slides = [];
    for (const id of childrenOf(list, 'sldId')) {
        const rel = rels.get(relId(id));
        if (!rel) continue;
        // eslint-disable-next-line no-await-in-loop
        const [xml, slideRels] = await Promise.all([readXml(zip, rel.target), readRels(zip, rel.target)]);
        const slide = { number: slides.length + 1, title: '', blocks: [], hidden: attr(findFirst(xml, 'sld'), 'show') === '0' };
        readShapes(findFirst(xml, 'spTree'), slide, slideRels, images);
        slides.push(slide);
    }
    const media = await loadImages(zip, images);
    return { slides, media, text: slidesToText(slides) };
}

