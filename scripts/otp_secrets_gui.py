# -*- coding: utf-8 -*-
"""Окно владельца для хранилища секретов (scripts/otp_secrets.py).

Запуск — ярлык «Секреты iCORE» на рабочем столе или `pythonw scripts/otp_secrets.py gui`.
Значения вводятся только здесь и сразу сохраняются зашифрованными; агент их не видит.
"""
import json
import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import otp_secrets as core

CLIPBOARD_CLEAR_MS = 30_000
TIER_TITLES = {'normal': 'обычный', 'critical': 'важный'}


def ask_passphrase(master, reason, confirm=False):
    """Пароль хранилища в окне поверх главного. None — отказ."""
    win = tk.Toplevel(master)
    win.title('Пароль хранилища')
    win.transient(master)
    win.resizable(False, False)
    win.attributes('-topmost', True)
    ttk.Label(win, text=reason, wraplength=460, justify='left').pack(anchor='w', padx=16, pady=(14, 8))
    fields = []
    for label in (['Пароль', 'Ещё раз'] if confirm else ['Пароль']):
        ttk.Label(win, text=label).pack(anchor='w', padx=16)
        entry = ttk.Entry(win, show='•', width=40)
        entry.pack(anchor='w', padx=16, pady=(0, 8))
        fields.append(entry)
    status = ttk.Label(win, text='', foreground='#b00020')
    status.pack(anchor='w', padx=16)
    result = {'value': None}

    def ok(_event=None):
        values = [f.get() for f in fields]
        if confirm and values[0] != values[1]:
            status.config(text='Пароли не совпали')
            return
        if confirm and len(values[0]) < 10:
            status.config(text='Не короче 10 символов')
            return
        result['value'] = values[0]
        win.destroy()

    row = ttk.Frame(win)
    row.pack(fill='x', padx=16, pady=12)
    ttk.Button(row, text='Отмена', command=win.destroy).pack(side='right')
    ttk.Button(row, text='OK', command=ok).pack(side='right', padx=8)
    win.bind('<Return>', ok)
    win.bind('<Escape>', lambda _e: win.destroy())
    fields[0].focus_force()
    win.grab_set()
    master.wait_window(win)
    return result['value']


class ValueDialog(tk.Toplevel):
    """Добавить или заменить значение. Однострочное — под точками, многострочное — в поле текста."""

    def __init__(self, app, name='', tier='normal', fixed_name=False):
        super().__init__(app.root)
        self.app, self.result = app, None
        self.title('Заменить значение' if fixed_name else 'Добавить ключ')
        self.transient(app.root)
        self.resizable(True, True)
        pad = {'padx': 16}
        ttk.Label(self, text='Имя (латиница в верхнем регистре, цифры и «_»)').pack(anchor='w', pady=(14, 0), **pad)
        self.name = ttk.Entry(self, width=48)
        self.name.insert(0, name)
        if fixed_name:
            self.name.state(['disabled'])
        self.name.pack(anchor='w', **pad)
        self.tier = tk.StringVar(value=tier)
        box = ttk.LabelFrame(self, text='Уровень')
        box.pack(fill='x', pady=10, **pad)
        ttk.Radiobutton(box, text='Обычный — открывается без пароля (Windows охраняет его вашей учёткой)',
                        variable=self.tier, value='normal').pack(anchor='w', padx=8, pady=2)
        ttk.Radiobutton(box, text='Важный — каждый раз спрашивать пароль хранилища (прод, облако, ключи выкладки)',
                        variable=self.tier, value='critical').pack(anchor='w', padx=8, pady=2)
        self.multi = tk.BooleanVar(value=False)
        self.show = tk.BooleanVar(value=False)
        opts = ttk.Frame(self)
        opts.pack(fill='x', **pad)
        ttk.Checkbutton(opts, text='Многострочное (ключ SSH, JSON, конфиг)', variable=self.multi,
                        command=self._switch).pack(side='left')
        ttk.Checkbutton(opts, text='Показать', variable=self.show, command=self._toggle_show).pack(side='left', padx=12)
        self.holder = ttk.Frame(self)
        self.holder.pack(fill='both', expand=True, pady=6, **pad)
        self.line = ttk.Entry(self.holder, show='•', width=60)
        self.text = tk.Text(self.holder, width=72, height=12, wrap='none')
        self.line.pack(fill='x')
        ttk.Label(self, text='Значение никуда не выводится и сразу сохраняется зашифрованным.',
                  foreground='#555').pack(anchor='w', **pad)
        row = ttk.Frame(self)
        row.pack(fill='x', pady=12, **pad)
        ttk.Button(row, text='Отмена', command=self.destroy).pack(side='right')
        ttk.Button(row, text='Сохранить', command=self._save).pack(side='right', padx=8)
        self.bind('<Escape>', lambda _e: self.destroy())
        (self.line if fixed_name else self.name).focus_force()
        self.grab_set()

    def _switch(self):
        if self.multi.get():
            self.line.pack_forget()
            self.text.pack(fill='both', expand=True)
            self.text.focus_force()
        else:
            self.text.pack_forget()
            self.line.pack(fill='x')
            self.line.focus_force()

    def _toggle_show(self):
        self.line.config(show='' if self.show.get() else '•')

    def _save(self):
        name = self.name.get().strip()
        value = self.text.get('1.0', 'end-1c') if self.multi.get() else self.line.get()
        if not core.NAME_RE.match(name):
            messagebox.showerror('Имя', 'Имя — латиница в верхнем регистре, цифры и «_».', parent=self)
            return
        if not value.strip():
            messagebox.showerror('Значение', 'Пустое значение не сохраняю.', parent=self)
            return
        tier = self.tier.get()
        if tier == 'critical' and not self.app.ensure_unlocked('Сохранить важный ключ %s' % name):
            return
        try:
            self.app.vault.reload()
            self.app.vault.put(name, value if self.multi.get() else value.strip(), tier)
        except core.VaultError as exc:
            messagebox.showerror('Не сохранено', str(exc), parent=self)
            return
        # Поле не храним в памяти дольше нужного.
        self.line.delete(0, 'end')
        self.text.delete('1.0', 'end')
        self.result = name
        self.destroy()


class App:
    def __init__(self):
        self.vault = core.Vault()
        self.root = tk.Tk()
        self.root.title('Секреты iCORE')
        self.root.geometry('900x600')
        try:
            ttk.Style().theme_use('vista')
        except tk.TclError:
            pass
        top = ttk.Frame(self.root)
        top.pack(fill='x', padx=12, pady=(12, 6))
        ttk.Label(top, text='Поиск').pack(side='left')
        self.query = tk.StringVar()
        self.query.trace_add('write', lambda *_: self.refresh())
        ttk.Entry(top, textvariable=self.query, width=32).pack(side='left', padx=8)
        self.banner = ttk.Label(top, text='', foreground='#b00020')
        self.banner.pack(side='left', padx=12)

        body = ttk.Frame(self.root)
        body.pack(fill='both', expand=True, padx=12)
        cols = ('tier', 'len', 'updated')
        self.tree = ttk.Treeview(body, columns=cols, show='tree headings', selectmode='browse')
        self.tree.heading('#0', text='Имя')
        self.tree.heading('tier', text='Уровень')
        self.tree.heading('len', text='Длина')
        self.tree.heading('updated', text='Изменён')
        self.tree.column('#0', width=380)
        self.tree.column('tier', width=110, anchor='center')
        self.tree.column('len', width=70, anchor='e')
        self.tree.column('updated', width=150, anchor='center')
        scroll = ttk.Scrollbar(body, orient='vertical', command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='left', fill='y')
        self.tree.bind('<Double-1>', lambda _e: self.replace())

        side = ttk.Frame(body)
        side.pack(side='left', fill='y', padx=(12, 0))
        for text, cmd in (('Добавить…', self.add), ('Заменить значение…', self.replace), ('Переименовать…', self.rename),
                          ('Важный / обычный', self.toggle_tier), ('Скопировать значение', self.copy),
                          ('Удалить', self.delete), (None, None), ('Пароль для важных…', self.passphrase),
                          ('Рекомендованные — в важные…', self.recommended), ('Перенести .env…', self.import_env),
                          (None, None), ('Резервная копия…', self.backup), ('Восстановить из копии…', self.restore),
                          ('Журнал доступа', self.show_log)):
            if text is None:
                ttk.Separator(side).pack(fill='x', pady=8)
                continue
            ttk.Button(side, text=text, command=cmd, width=28).pack(fill='x', pady=2)
        self.status = ttk.Label(self.root, text='', foreground='#555')
        self.status.pack(fill='x', padx=12, pady=8)
        self.refresh()

    # общее
    def selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo('Секреты', 'Выберите ключ в списке.', parent=self.root)
            return None
        return sel[0]

    def refresh(self):
        self.vault.reload()
        q = self.query.get().strip().upper()
        self.tree.delete(*self.tree.get_children())
        entries = self.vault.entries()
        for name in sorted(entries):
            if q and q not in name:
                continue
            e = entries[name]
            tier = TIER_TITLES[e['tier']] + (' · архив' if e.get('kind') == 'archive' else '')
            self.tree.insert('', 'end', iid=name, text=name,
                             values=(tier, e['len'], e['updated'][:16].replace('T', ' ')))
        crit = sum(1 for e in entries.values() if e['tier'] == 'critical')
        self.banner.config(text='' if self.vault.has_passphrase() else
                           'Пароль для важных не задан — задайте, чтобы закрыть их без вас')
        self.status.config(text='Ключей %d, важных %d · %s' % (len(entries), crit, self.vault.file))

    def ensure_unlocked(self, reason):
        if not self.vault.has_passphrase():
            new = ask_passphrase(self.root, 'Задайте пароль хранилища для важных ключей. Его знаете только вы: '
                                            'без него важные ключи не откроет никто, в том числе агент. '
                                            'Потеряете — важные ключи придётся завести заново.', confirm=True)
            if not new:
                return False
            try:
                self.vault.set_passphrase(new)
            except core.VaultError as exc:
                messagebox.showerror('Пароль', str(exc), parent=self.root)
                return False
            return True
        if self.vault.unlocked():
            return True
        for _ in range(3):
            value = ask_passphrase(self.root, reason)
            if value is None:
                return False
            try:
                self.vault.unlock(value)
                return True
            except core.VaultError as exc:
                reason = '%s. Ещё раз.' % exc
        return False

    # действия
    def add(self):
        dlg = ValueDialog(self)
        self.root.wait_window(dlg)
        self.refresh()

    def replace(self):
        name = self.selected()
        if not name:
            return
        dlg = ValueDialog(self, name=name, tier=self.vault.entries()[name]['tier'], fixed_name=True)
        self.root.wait_window(dlg)
        self.refresh()

    def rename(self):
        name = self.selected()
        if not name:
            return
        new = simpledialog.askstring('Переименовать', 'Новое имя для %s' % name, parent=self.root)
        if not new:
            return
        if self.vault.entries()[name]['tier'] == 'critical' and not self.ensure_unlocked('Переименовать %s' % name):
            return
        try:
            self.vault.rename(name, new.strip())
        except core.VaultError as exc:
            messagebox.showerror('Не переименовано', str(exc), parent=self.root)
        self.refresh()

    def toggle_tier(self):
        name = self.selected()
        if not name:
            return
        tier = self.vault.entries()[name]['tier']
        target = 'normal' if tier == 'critical' else 'critical'
        if target == 'normal' and not messagebox.askyesno(
                'Сделать обычным', '%s станет открываться без пароля хранилища. Продолжить?' % name, parent=self.root):
            return
        if not self.ensure_unlocked('Изменить уровень %s' % name):
            return
        try:
            self.vault.set_tier(name, target)
        except core.VaultError as exc:
            messagebox.showerror('Не изменено', str(exc), parent=self.root)
        self.refresh()

    def copy(self):
        name = self.selected()
        if not name:
            return
        if self.vault.entries()[name]['tier'] == 'critical' and not self.ensure_unlocked('Скопировать %s' % name):
            return
        try:
            value = self.vault.value(name)
        except core.VaultError as exc:
            messagebox.showerror('Не скопировано', str(exc), parent=self.root)
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.vault.log('copy', [name], by='gui')

        def clear():
            try:
                if self.root.clipboard_get() == value:
                    self.root.clipboard_clear()
            except tk.TclError:
                pass
        self.root.after(CLIPBOARD_CLEAR_MS, clear)
        self.status.config(text='%s скопирован — буфер очистится через 30 секунд' % name)

    def delete(self):
        name = self.selected()
        if not name:
            return
        if messagebox.askyesno('Удалить', 'Удалить %s? Вернуть можно только из резервной копии.' % name, parent=self.root):
            self.vault.delete(name)
            self.refresh()

    def passphrase(self):
        if self.vault.has_passphrase():
            if not self.ensure_unlocked('Текущий пароль хранилища'):
                return
            new = ask_passphrase(self.root, 'Новый пароль хранилища', confirm=True)
            if not new:
                return
            try:
                self.vault.set_passphrase(new)
            except core.VaultError as exc:
                messagebox.showerror('Пароль', str(exc), parent=self.root)
                return
            messagebox.showinfo('Пароль', 'Пароль сменён, важные ключи перешифрованы.', parent=self.root)
        else:
            if self.ensure_unlocked(''):
                messagebox.showinfo('Пароль', 'Пароль задан. Теперь отметьте важные ключи.', parent=self.root)
        self.refresh()

    def recommended(self):
        entries = self.vault.entries()
        names = [n for n in core.RECOMMENDED_CRITICAL if n in entries and entries[n]['tier'] != 'critical']
        names += [n for n, e in entries.items() if e.get('kind') == 'archive' and e['tier'] != 'critical']
        if not names:
            messagebox.showinfo('Рекомендованные', 'Всё рекомендованное уже важное.', parent=self.root)
            return
        if not messagebox.askyesno('Рекомендованные — в важные',
                                   'Сделать важными (каждый раз — ваш пароль):\n\n' + '\n'.join(names), parent=self.root):
            return
        if not self.ensure_unlocked('Пароль хранилища'):
            return
        for name in names:
            self.vault.set_tier(name, 'critical')
        self.refresh()

    def import_env(self):
        path = filedialog.askopenfilename(title='Файл .env для переноса', parent=self.root)
        if not path:
            return
        try:
            parsed = core.parse_env_text(Path(path).read_text(encoding='utf-8'))
        except (OSError, core.VaultError, UnicodeDecodeError) as exc:
            messagebox.showerror('Не прочитан', str(exc), parent=self.root)
            return
        names = [p[0] for p in parsed]
        clash = sorted(set(n for n in names if n in self.vault.entries()))
        text = 'Ключей в файле: %d.' % len(set(names))
        if clash:
            text += '\nУже есть в хранилище (будут заменены): %s' % ', '.join(clash[:20])
        if not messagebox.askyesno('Перенести .env', text + '\n\nПеренести? Файл целиком ляжет в архив хранилища.',
                                   parent=self.root):
            return
        for name, value, _line, kind in parsed:
            if kind == 'multiline':
                value = core.wrap_openssh(value)
            self.vault._write(name, value, self.vault.entries().get(name, {}).get('tier', 'normal')
                              if self.vault.unlocked() else 'normal', save=False, kind='env')
        archive = '%s%s' % (core.ARCHIVE_PREFIX, core._slug(Path(path).name) or 'ENV')
        self.vault._write(archive, Path(path).read_text(encoding='utf-8'), 'normal', save=False, kind='archive')
        self.vault.save()
        self.vault.log('import-env', names, source=path, by='gui')
        if messagebox.askyesno('Перенесено', 'Перенесено. Удалить исходный файл с открытыми паролями?', parent=self.root):
            try:
                os.remove(path)
            except OSError as exc:
                messagebox.showerror('Не удалён', str(exc), parent=self.root)
        self.refresh()

    def backup(self):
        if any(e['tier'] == 'critical' for e in self.vault.entries().values()) and \
                not self.ensure_unlocked('Для копии нужны и важные ключи — пароль хранилища'):
            return
        path = filedialog.asksaveasfilename(title='Резервная копия', defaultextension='.otpbackup',
                                            initialfile='secrets-backup.otpbackup', parent=self.root)
        if not path:
            return
        pw = ask_passphrase(self.root, 'Пароль для этой копии (отдельный; без него копию не открыть — храните его не на этом ПК)',
                            confirm=True)
        if not pw:
            return
        try:
            count = core.export_backup(self.vault, path, pw)
        except core.VaultError as exc:
            messagebox.showerror('Копия', str(exc), parent=self.root)
            return
        messagebox.showinfo('Копия', 'Сохранено ключей: %d\n%s' % (count, path), parent=self.root)

    def restore(self):
        path = filedialog.askopenfilename(title='Резервная копия', filetypes=[('Копия', '*.otpbackup'), ('Все', '*.*')],
                                          parent=self.root)
        if not path:
            return
        pw = ask_passphrase(self.root, 'Пароль этой копии')
        if not pw:
            return
        try:
            entries = core.read_backup(path, pw)
        except (core.VaultError, OSError, ValueError) as exc:
            messagebox.showerror('Копия', str(exc), parent=self.root)
            return
        overwrite = messagebox.askyesno('Восстановить', 'В копии ключей: %d. Заменять те, что уже есть?' % len(entries),
                                        parent=self.root)
        if any(i['tier'] == 'critical' for i in entries.values()):
            self.ensure_unlocked('В копии есть важные ключи — пароль хранилища')
        added, skipped = core.import_backup(self.vault, entries, overwrite)
        messagebox.showinfo('Восстановлено', 'Добавлено %d, пропущено %d.' % (len(added), len(skipped)), parent=self.root)
        self.refresh()

    def show_log(self):
        win = tk.Toplevel(self.root)
        win.title('Журнал доступа')
        win.geometry('900x480')
        text = tk.Text(win, wrap='none')
        text.pack(fill='both', expand=True)
        lines = []
        if self.vault.log_file.exists():
            for raw in self.vault.log_file.read_text(encoding='utf-8').splitlines()[-400:]:
                try:
                    r = json.loads(raw)
                except ValueError:
                    continue
                extra = r.get('command') or r.get('source') or r.get('reason') or ''
                lines.append('%s  %-14s %s  %s' % (r.get('time', '')[:19].replace('T', ' '), r.get('action', ''),
                                                   ', '.join(r.get('names', [])[:8]) + (' …' if len(r.get('names', [])) > 8 else ''),
                                                   extra))
        text.insert('1.0', '\n'.join(reversed(lines)) or 'Журнал пуст')
        text.config(state='disabled')

    def run(self):
        self.root.mainloop()


def run_gui():
    App().run()
    return 0


if __name__ == '__main__':
    run_gui()
