#!/usr/bin/env python3
"""Run without arguments for a window, or use --input / --demo."""
from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

from ranging.demo import demo_result
from ranging.engine import analyze, load_history, save_history
from ranging.io import read_inputs
from ranging.report import export_report

HERE = Path(__file__).resolve().parent


def run_job(source, output: Path, inspect_pages: bool = True, demo: bool = False,
            cancel=None, log=print):
    output.mkdir(parents=True, exist_ok=True)
    if demo:
        result = demo_result()
        history = None
    else:
        inputs, warnings = read_inputs(source)
        history = load_history(output / 'history.json', warnings)
        result = analyze(inputs, HERE / '.cache', history=history,
                         inspect_pages=inspect_pages, cancel=cancel, log=log)
        result['warnings'].extend(warnings)
    paths = export_report(result, output)
    if history is not None:
        # Keep a corrupted history file for recovery, rather than silently replacing it.
        history_file = output / 'history.json'
        if any('История не читается' in w for w in result['warnings']) and history_file.exists():
            backup = history_file.with_suffix('.broken.json')
            if not backup.exists():
                history_file.rename(backup)
            else:
                log('История не сохранена: уже есть history.broken.json. Отчёт создан.')
                return paths, result
        try:
            save_history(history_file, history, result)
        except OSError as exc:
            log(f'Отчёт создан, но история не сохранена: {exc}')
    return paths, result


def gui():
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        from tkinter.scrolledtext import ScrolledText
        root = tk.Tk()
    except Exception as exc:
        print(f'Окно недоступно: {exc}\nИспользуйте: python app.py --input forums.txt', file=sys.stderr)
        return 1
    root.title('Website Ranging — приоритет форумов')
    root.geometry('820x740')
    root.minsize(700, 650)
    root.option_add('*Font', 'TkDefaultFont 10')
    frame = ttk.Frame(root, padding=16); frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Какие форумы проверить первыми?', font=('TkDefaultFont', 17, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='Вставьте ссылки по одной на строке или откройте TXT / CSV / XLSX.').pack(anchor='w', pady=(8, 5))
    input_box = ScrolledText(frame, height=10, wrap='none'); input_box.pack(fill='both', expand=True)
    file_path = {'value': None}
    file_label = tk.StringVar(value='Можно вставлять ссылки прямо в поле выше.')
    buttons = ttk.Frame(frame); buttons.pack(fill='x', pady=8)

    def choose_file():
        filename = filedialog.askopenfilename(filetypes=[('Список форумов', '*.txt *.csv *.xlsx *.tsv'), ('Все файлы', '*.*')])
        if not filename:
            return
        try:
            rows, _ = read_inputs(Path(filename))
            input_box.delete('1.0', 'end')
            input_box.insert('1.0', '\n'.join(row['input_url'] for row in rows))
            input_box.edit_modified(False)
            file_path['value'] = Path(filename)
            file_label.set(f'Файл: {Path(filename).name}. Изменение текста переключает на ручной ввод.')
        except Exception as exc:
            messagebox.showerror('Не удалось прочитать файл', str(exc))

    def input_changed(_event):
        if input_box.edit_modified():
            file_path['value'] = None
            file_label.set('Ручной ввод ссылок')
            input_box.edit_modified(False)
    input_box.bind('<<Modified>>', input_changed)
    load_button = ttk.Button(buttons, text='Открыть список из файла', command=choose_file); load_button.pack(side='left')
    ttk.Label(frame, textvariable=file_label, wraplength=770).pack(anchor='w')
    inspect = tk.BooleanVar(value=True)
    check = ttk.Checkbutton(frame, text='Проверять открытые страницы форумов (без обхода авторизации)', variable=inspect)
    check.pack(anchor='w', pady=(10, 3))
    ttk.Label(frame, text='CrUX + Majestic — без ключей. Первая загрузка справочников может занять около 100 МБ трафика.',
              wraplength=770).pack(anchor='w')
    output_var = tk.StringVar(value=str(HERE / 'results'))
    output_row = ttk.Frame(frame); output_row.pack(fill='x', pady=8)
    ttk.Label(output_row, text='Папка отчётов:').pack(side='left')
    output_entry = ttk.Entry(output_row, textvariable=output_var); output_entry.pack(side='left', fill='x', expand=True, padx=8)
    def choose_output():
        folder = filedialog.askdirectory()
        if folder:
            output_var.set(folder)
    output_button = ttk.Button(output_row, text='Выбрать', command=choose_output); output_button.pack(side='right')
    status = tk.StringVar(value='Готово к запуску. «Демо» покажет пример отчёта без обращения к сети.')
    progress = ttk.Progressbar(frame, mode='indeterminate'); progress.pack(fill='x', pady=8)
    ttk.Label(frame, textvariable=status, wraplength=770).pack(anchor='w')
    logs = ScrolledText(frame, height=8, state='disabled'); logs.pack(fill='both', expand=True, pady=8)
    actions = ttk.Frame(frame); actions.pack(fill='x')
    events = queue.Queue()
    state = {'busy': False, 'cancel': None, 'closing': False, 'last_output': None}

    def log(message):
        events.put(('log', str(message)))

    def set_busy(busy):
        state['busy'] = busy
        for widget in (start_button, demo_button, load_button, output_button, output_entry, check):
            widget.configure(state='disabled' if busy else 'normal')
        input_box.configure(state='disabled' if busy else 'normal')
        stop_button.configure(state='normal' if busy else 'disabled')
        progress.start(12) if busy else progress.stop()

    def start(demo=False):
        source = file_path['value'] or input_box.get('1.0', 'end')
        if not demo:
            try:
                read_inputs(source)
            except Exception as exc:
                messagebox.showerror('Проверьте список', str(exc)); return
        if not output_var.get().strip():
            messagebox.showerror('Папка отчётов', 'Выберите папку для результатов.'); return
        output = Path(output_var.get()).expanduser()
        pages = inspect.get()
        cancel = threading.Event(); state['cancel'] = cancel
        set_busy(True); status.set('Проверка началась…')
        logs.configure(state='normal'); logs.delete('1.0', 'end'); logs.configure(state='disabled')
        def worker():
            try:
                paths, result = run_job(source, output, pages, demo, cancel, log)
                events.put(('done', (paths, result)))
            except Exception as exc:
                events.put(('error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def stop():
        if state['cancel']:
            state['cancel'].set()
            status.set('Остановка после текущего запроса. Частичный отчёт будет сохранён.')

    def open_output():
        path = state['last_output'] or Path(output_var.get()).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            if sys.platform == 'win32':
                os.startfile(str(path))
            else:
                subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)])
        except OSError as exc:
            messagebox.showerror('Не удалось открыть папку', str(exc))

    start_button = ttk.Button(actions, text='Проверить форумы', command=start); start_button.pack(side='left')
    stop_button = ttk.Button(actions, text='Остановить', command=stop, state='disabled'); stop_button.pack(side='left', padx=6)
    demo_button = ttk.Button(actions, text='Демо без Интернета', command=lambda: start(True)); demo_button.pack(side='left')
    ttk.Button(actions, text='Открыть отчёты', command=open_output).pack(side='right')

    def poll():
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == 'log':
                    status.set(value); logs.configure(state='normal')
                    logs.insert('end', value + '\n'); logs.see('end'); logs.configure(state='disabled')
                elif kind == 'error':
                    set_busy(False); status.set('Не удалось завершить: ' + value)
                    if not state['closing']:
                        messagebox.showerror('Ошибка', value)
                else:
                    paths, result = value
                    state['last_output'] = paths['xlsx'].parent
                    set_busy(False)
                    unknown = sum(r['coverage'] < 60 for r in result['rows'])
                    text = (f'Сохранён {"частичный " if result["cancelled"] else ""}отчёт: {paths["xlsx"].name}\n'
                            f'Форумов: {len(result["rows"])}. Требуют дополнительных данных: {unknown}.')
                    status.set(text)
                    if not state['closing']:
                        messagebox.showinfo('Отчёт создан', text + '\nНажмите «Открыть отчёты».')
        except queue.Empty:
            pass
        if state['closing'] and not state['busy']:
            root.destroy(); return
        root.after(100, poll)

    def close():
        if state['busy']:
            if messagebox.askyesno('Остановить проверку?', 'Остановить и сохранить частичный отчёт перед закрытием?'):
                state['closing'] = True; stop()
        else:
            root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    root.after(100, poll)
    root.mainloop()
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Бесплатное предварительное ранжирование форумов. Без аргументов — окно.')
    parser.add_argument('--input', type=Path, help='TXT, CSV или XLSX со ссылками')
    parser.add_argument('--output', type=Path, default=HERE / 'results', help='Папка отчётов и истории')
    parser.add_argument('--no-pages', action='store_true', help='Только справочники, не обращаться к форумам')
    parser.add_argument('--demo', action='store_true', help='Пример с вымышленными данными, без сети')
    args = parser.parse_args(argv)
    if not args.input and not args.demo:
        return gui()
    try:
        paths, result = run_job(args.input, args.output, not args.no_pages, args.demo)
        print(f'Excel: {paths["xlsx"]}\nCSV: {paths["csv"]}')
        print(f'Строк: {len(result["rows"])}; нужны данные: {sum(r["coverage"] < 60 for r in result["rows"])}')
        return 0
    except Exception as exc:
        print(f'Ошибка: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
