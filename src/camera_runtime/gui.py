"""Small Windows front end. Worker commands remain independently reviewable."""
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog,messagebox,ttk

BASE=Path(__file__).resolve().parent

def main():
    root=tk.Tk();root.title('A7C II · FL2 / FL3 프리뷰');root.geometry('780x620')
    root.minsize(720,540);q=queue.Queue();running=[False]
    work=tk.StringVar();backup=tk.StringVar();battery=tk.StringVar();version=tk.StringVar()
    wrap=ttk.Frame(root,padding=22);wrap.pack(fill='both',expand=True)
    ttk.Label(wrap,text='A7C II · FL2 / FL3',font=('Microsoft JhengHei UI',22,'bold')).pack(anchor='w')
    ttk.Label(wrap,text='A7C II 2.01｜펌웨어 출처 미검사｜개발 버전, 실기 검증 대기',wraplength=700).pack(anchor='w',pady=(5,14))
    ttk.Label(wrap,text='이 저장소가 아닌 곳에서 수정한 펌웨어는 충돌할 수 있습니다. 먼저 백업하며, FL23이 이미 활성화되어 있으면 중복 설치하지 않습니다.',wraplength=700).pack(anchor='w',pady=(0,12))
    controls=[]
    for title,var in [('카메라 작업 폴더(처음에는 빈 폴더 선택)',work),('두 번째 백업(다른 디스크의 빈 폴더 권장)',backup)]:
        ttk.Label(wrap,text=title).pack(anchor='w')
        row=ttk.Frame(wrap);row.pack(fill='x',pady=(3,9))
        entry=ttk.Entry(row,textvariable=var);entry.pack(side='left',fill='x',expand=True)
        def choose(v=var):
            path=filedialog.askdirectory()
            if path:v.set(path)
        button=ttk.Button(row,text='선택',command=choose);button.pack(side='left',padx=(8,0));controls.extend([entry,button])
    row=ttk.Frame(wrap);row.pack(fill='x',pady=(0,12))
    ttk.Label(row,text='현재 카메라 배터리: ').pack(side='left')
    entry=ttk.Entry(row,width=7,textvariable=battery);entry.pack(side='left')
    ttk.Label(row,text='%   쓰기에는 최소 50%가 필요하며, 그동안 USB 연결을 유지하세요.').pack(side='left');controls.append(entry)
    row=ttk.Frame(wrap);row.pack(fill='x',pady=(0,8))
    ttk.Label(row,text='카메라 버전(카메라 버전 화면을 보고 입력): ').pack(side='left')
    entry=ttk.Entry(row,width=8,textvariable=version);entry.pack(side='left');controls.append(entry)
    status=tk.StringVar(value='먼저 설명을 읽고 두 USB ID의 드라이버가 준비되었는지 확인하세요.')
    ttk.Label(wrap,textvariable=status,wraplength=700).pack(anchor='w',pady=(0,8))
    progress=ttk.Progressbar(wrap,mode='indeterminate');progress.pack(fill='x')
    buttons=ttk.Frame(wrap);buttons.pack(fill='x',pady=12)
    log=tk.Text(wrap,height=13,wrap='word',state='disabled',font=('Microsoft JhengHei UI',10));log.pack(fill='both',expand=True)
    def run(command):
        if running[0]:return
        if not work.get():messagebox.showinfo('작업 폴더 선택','먼저 카메라 작업 폴더를 선택하세요.');return
        cmd=[sys.executable,'-u',str(BASE/'app.py'),command,'--workspace',work.get()]
        if command=='prepare':
            if not backup.get():messagebox.showinfo('백업 폴더 선택','먼저 두 번째 백업 폴더를 선택하세요.');return
            if version.get().strip()!='2.01':messagebox.showinfo('카메라 버전','이 도구는 2.01 전용입니다. 먼저 카메라 버전 화면을 확인하고 입력하세요.');return
            cmd+=['--backup-copy',backup.get(),'--firmware-version',version.get().strip()]
        if command=='install':
            try:value=int(battery.get())
            except ValueError:value=-1
            if not 50<=value<=100:messagebox.showinfo('배터리 확인','실제 배터리 잔량을 입력하세요. 최소 50%가 필요합니다.');return
            cmd+=['--battery-percent',str(value)]
        if command=='install':
            messagebox.showwarning('펌웨어 호환성 알림','펌웨어 출처를 검증하지 않습니다. 이 저장소가 아닌 곳에서 수정한 펌웨어는 FL23과 충돌해 카메라가 정상적으로 부팅되지 않을 수 있습니다. 이 컴퓨터의 백업을 보관하세요.')
        running[0]=True;progress.start(12);status.set('처리 중입니다. 케이블을 뽑거나 전원을 끄거나 창을 닫지 마세요.')
        for c in controls:c.configure(state='disabled')
        def worker():
            try:
                env=dict(os.environ,PYTHONIOENCODING='utf-8')
                flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
                with subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,
                                      encoding='utf-8',errors='replace',env=env,cwd=BASE,creationflags=flags) as process:
                    for line in process.stdout:q.put(('log',line))
                    q.put(('done',process.wait()))
            except Exception as exc:q.put(('log',str(exc)+'\n'));q.put(('done',1))
        threading.Thread(target=worker,daemon=True).start()
    for title,command in [('1 백업 및 검사','prepare'),('2 프리뷰 설치','install'),('3 재시작 후 확인','verify')]:
        button=ttk.Button(buttons,text=title,command=lambda c=command:run(c));button.pack(side='left',padx=(0,7));controls.append(button)
    def tick():
        try:
            while True:
                kind,value=q.get_nowait()
                if kind=='log':log.configure(state='normal');log.insert('end',value);log.see('end');log.configure(state='disabled')
                else:
                    running[0]=False;progress.stop()
                    for c in controls:c.configure(state='normal')
                    status.set('처리 완료. 아래 결과와 다음 단계를 읽어 보세요.' if value==0 else '중지되었습니다. 작업 폴더를 보존하고 오류 내용에 따라 상태를 확인하세요. 무작정 쓰기를 재실행하지 마세요.')
        except queue.Empty:pass
        root.after(100,tick)
    def close():
        if running[0]:messagebox.showinfo('아직 처리 중','읽기/쓰기 과정이 아직 끝나지 않았습니다. 결과를 기다리세요.')
        else:root.destroy()
    root.protocol('WM_DELETE_WINDOW',close);tick();root.mainloop()
if __name__=='__main__':main()
