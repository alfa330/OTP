/* Кадр потока колокола (/api/notifications/stream, notifications/routes.py):

     : connected <время> <процесс> <курсор> <продолжен 0|1>
     id: <курсор> / event: reload / data: {"b":0|1}
     : heartbeat <время> <курсор>

   Процесс и курсор клиент отдаёт обратно заголовком Last-Event-ID, когда
   вкладка возвращается из фона: продолженному потоку сводку перечитывать
   незачем — пропущенное сервер пришлёт reload'ом. b — был ли среди тычков
   широковещательный (новость дня, «Ивенты», «4 You»): только тогда есть смысл
   спрашивать /api/news/pending (27.10.2026).

   Сервер до 27.10.2026 слал те же строки без хвоста. Тогда процесса и курсора
   нет, поток считается непродолженным, а тычок — широковещательным: ровно
   прежнее поведение. */
export const parseBellFrame = (chunk) => {
    const frame = { kind: null, epoch: null, seq: null, resumed: false, broadcast: true };
    const toSeq = (value) => (/^\d+$/.test(value || '') ? Number(value) : null);
    for (const line of String(chunk || '').split('\n')) {
        if (line.startsWith(': connected')) {
            const [, , , epoch, seq, resumed] = line.split(' ');
            frame.kind = 'connected';
            frame.epoch = epoch || null;
            frame.seq = toSeq(seq);
            frame.resumed = Boolean(frame.epoch) && frame.seq !== null && resumed === '1';
        } else if (line.startsWith(': heartbeat')) {
            frame.kind = 'heartbeat';
            frame.seq = toSeq(line.split(' ')[3]);
        } else if (line.startsWith('id: ')) {
            frame.seq = toSeq(line.slice(4).trim());
        } else if (line.startsWith('event: reload')) {
            frame.kind = 'reload';
        } else if (line.startsWith('data: ')) {
            try {
                const data = JSON.parse(line.slice(6));
                if (data && data.b === 0) frame.broadcast = false;
            } catch {
                /* непонятные данные — считаем тычок широковещательным, как раньше */
            }
        }
    }
    return frame;
};

/* Заголовок продолжения для следующего подключения; пусто — поток с нуля. */
export const bellResumeHeaders = (resume) => (
    resume?.epoch && Number.isInteger(resume?.seq) ? { 'Last-Event-ID': `${resume.epoch}:${resume.seq}` } : {}
);
