#!/usr/bin/env python3
"""
Gerador de clips automático e gratuito.

Fluxo: link/arquivo -> Whisper (transcrição) -> LLM escolhe os melhores
trechos -> FFmpeg corta (opcionalmente vertical 9:16 com legendas).

INSTALAÇÃO (uma vez):
    pip install faster-whisper yt-dlp requests
    # FFmpeg: https://ffmpeg.org/download.html  (Windows: winget install ffmpeg)

LLM (escolha um, ambos gratuitos):
    A) Ollama (local):  instale em https://ollama.com e rode:  ollama pull llama3.1
    B) Gemini (API grátis): crie uma chave em https://aistudio.google.com
       e defina:  export GEMINI_API_KEY="sua_chave"   (Windows: setx GEMINI_API_KEY "sua_chave")

USO:
    python gerar_clips.py "https://youtube.com/watch?v=XXXX" --llm ollama --vertical --subs
    python gerar_clips.py video.mp4 --llm gemini -n 8 --min 25 --max 60
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

PROMPT = """Você é um editor de vídeos virais para TikTok, Reels e Shorts.
Abaixo está um trecho da transcrição de um vídeo, com o tempo em segundos entre colchetes.

Escolha até {k} trechos que funcionariam bem como clips independentes:
- começam com um gancho forte (pergunta, afirmação ousada, história)
- têm começo, meio e fim, sem depender do resto do vídeo
- duram entre {min_s} e {max_s} segundos
- começam e terminam em limites de frase

Responda SOMENTE com um JSON neste formato, sem texto extra:
[{{"start": 123.4, "end": 170.0, "score": 8, "title": "título curto", "reason": "por que é bom"}}]
"score" vai de 1 a 10. Se nada prestar, responda [].

TRANSCRIÇÃO:
{text}
"""


# ---------------------------------------------------------------- utilidades
def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def slug(s, n=40):
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "_", s)[:n] or "clip"


def srt_time(t):
    t = max(t, 0)
    h, r = divmod(int(t), 3600)
    m, s = divmod(r, 60)
    ms = int((t - int(t)) * 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


# ------------------------------------------------------------------ etapas
def baixar(source, workdir, cookies=None):
    if not source.startswith(("http://", "https://")):
        return Path(source)
    print("[1/4] Baixando vídeo...")
    out = workdir / "video.%(ext)s"
    cmd = ["yt-dlp", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
           "--merge-output-format", "mp4", "-o", str(out)]
    if cookies:
        cmd += ["--cookies", cookies]
    run(cmd + [source])
    return next(workdir.glob("video.*"))


def transcrever(video, model_size):
    print(f"[2/4] Transcrevendo com Whisper ({model_size})...")
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="auto", compute_type="auto")
    segs, info = model.transcribe(str(video), vad_filter=True)
    out = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segs]
    print(f"      idioma: {info.language} | {len(out)} segmentos")
    return out


def janelas(segs, minutos=10):
    """Divide a transcrição em janelas para caber em modelos pequenos."""
    chunks, cur, t0 = [], [], None
    for s in segs:
        if t0 is None:
            t0 = s["start"]
        cur.append(s)
        if s["end"] - t0 >= minutos * 60:
            chunks.append(cur)
            cur, t0 = [], None
    if cur:
        chunks.append(cur)
    return chunks


def chamar_llm(prompt, args):
    if args.llm == "gemini":
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            sys.exit("Defina a variável GEMINI_API_KEY.")
        r = requests.post(
            GEMINI_URL.format(model=args.model_llm or "gemini-2.5-flash"),
            params={"key": key},
            json={"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"responseMimeType": "application/json"}},
            timeout=180,
        )
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]

    r = requests.post(
        OLLAMA_URL,
        json={"model": args.model_llm or "llama3.1", "prompt": prompt,
              "stream": False, "format": "json",
              "options": {"num_ctx": 16384, "temperature": 0.3}},
        timeout=600,
    )
    r.raise_for_status()
    return r.json()["response"]


def parse_json(txt):
    txt = txt.strip()
    try:
        data = json.loads(txt)
    except json.JSONDecodeError:
        m = re.search(r"\[.*\]", txt, re.S)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    if isinstance(data, dict):  # alguns modelos embrulham a lista
        data = next((v for v in data.values() if isinstance(v, list)), [])
    return [d for d in data if isinstance(d, dict)]


def escolher_trechos(segs, args):
    print("[3/4] A IA está escolhendo os melhores momentos...")
    candidatos = []
    for i, chunk in enumerate(janelas(segs), 1):
        texto = "\n".join(f"[{s['start']:.1f}] {s['text']}" for s in chunk)
        prompt = PROMPT.format(k=3, min_s=args.min, max_s=args.max, text=texto)
        try:
            itens = parse_json(chamar_llm(prompt, args))
        except Exception as e:
            print(f"      aviso: janela {i} falhou ({e})")
            continue
        for it in itens:
            try:
                it["start"], it["end"] = float(it["start"]), float(it["end"])
                it["score"] = float(it.get("score", 5))
            except (KeyError, TypeError, ValueError):
                continue
            candidatos.append(it)

    # ajusta aos limites de frase e filtra duração
    validos = []
    for c in candidatos:
        ini = min(segs, key=lambda s: abs(s["start"] - c["start"]))["start"]
        fim = min(segs, key=lambda s: abs(s["end"] - c["end"]))["end"]
        dur = fim - ini
        if args.min * 0.7 <= dur <= args.max * 1.3:
            c["start"], c["end"] = ini, fim
            validos.append(c)

    # melhores primeiro, sem sobreposição
    validos.sort(key=lambda c: c["score"], reverse=True)
    final = []
    for c in validos:
        if all(c["end"] <= f["start"] or c["start"] >= f["end"] for f in final):
            final.append(c)
        if len(final) >= args.n:
            break
    return sorted(final, key=lambda c: c["start"])


def cortar(video, trechos, segs, outdir, args):
    print(f"[4/4] Cortando {len(trechos)} clips...")
    for i, c in enumerate(trechos, 1):
        nome = f"clip{i:02}_{slug(c.get('title', ''))}"
        mp4 = f"{nome}.mp4"
        filtros = []
        if args.vertical:
            filtros.append("crop=ih*9/16:ih,scale=1080:1920")
        if args.subs:
            srt = f"{nome}.srt"
            linhas = []
            n = 0
            for s in segs:
                if s["end"] > c["start"] and s["start"] < c["end"]:
                    n += 1
                    a = max(s["start"], c["start"]) - c["start"]
                    b = min(s["end"], c["end"]) - c["start"]
                    linhas.append(f"{n}\n{srt_time(a)} --> {srt_time(b)}\n{s['text']}\n")
            (outdir / srt).write_text("\n".join(linhas), encoding="utf-8")
            filtros.append(
                f"subtitles={srt}:force_style='FontName=Arial,FontSize=11,Bold=1,"
                f"Outline=2,Alignment=2,MarginV=60'"
            )
        cmd = ["ffmpeg", "-y", "-loglevel", "error",
               "-ss", f"{c['start']:.2f}", "-to", f"{c['end']:.2f}",
               "-i", str(video.resolve())]
        if filtros:
            cmd += ["-vf", ",".join(filtros)]
        cmd += ["-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-c:a", "aac", "-b:a", "160k", mp4]
        run(cmd, cwd=outdir)  # cwd = pasta de saída (evita problemas de caminho no filtro)
        print(f"      ✓ {mp4}  ({c['end'] - c['start']:.0f}s, nota {c['score']:.0f}) - {c.get('reason', '')}")


# -------------------------------------------------------------------- main
def main():
    p = argparse.ArgumentParser(description="Gerador de clips com IA (gratuito)")
    p.add_argument("source", help="link (YouTube etc.) ou caminho de um vídeo")
    p.add_argument("-n", type=int, default=5, help="quantidade de clips (padrão 5)")
    p.add_argument("--min", type=int, default=20, help="duração mínima em s")
    p.add_argument("--max", type=int, default=60, help="duração máxima em s")
    p.add_argument("--llm", choices=["ollama", "gemini"], default="ollama")
    p.add_argument("--model-llm", help="modelo do LLM (padrão: llama3.1 / gemini-2.5-flash)")
    p.add_argument("--whisper", default="small", help="tiny, base, small, medium, large-v3")
    p.add_argument("--vertical", action="store_true", help="formato 9:16 (corte central)")
    p.add_argument("--subs", action="store_true", help="queimar legendas no vídeo")
    p.add_argument("--cookies", help="arquivo cookies.txt (ajuda quando o YouTube bloqueia)")
    p.add_argument("-o", "--out", default="clips_saida", help="pasta de saída")
    args = p.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    video = baixar(args.source, outdir, args.cookies)
    segs = transcrever(video, args.whisper)
    (outdir / "transcricao.json").write_text(
        json.dumps(segs, ensure_ascii=False, indent=2), encoding="utf-8")

    trechos = escolher_trechos(segs, args)
    if not trechos:
        sys.exit("Nenhum trecho encontrado. Tente outro modelo ou ajuste --min/--max.")
    cortar(video, trechos, segs, outdir, args)
    print(f"\nPronto! Clips em: {outdir.resolve()}")


if __name__ == "__main__":
    main()
