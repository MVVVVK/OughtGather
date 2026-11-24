"""Manual CI-only public E5 smoke test; write aggregate metadata, never article data."""
import json
import math
import resource
import time
from pathlib import Path
from .e5 import E5Encoder, MODEL, REVISION

FILES = ('config.json', 'model.safetensors', 'tokenizer.json', 'tokenizer_config.json',
         'sentencepiece.bpe.model', 'special_tokens_map.json')
# Invented examples; no production or publisher text.
TITLES = (
    'A coastal city opened a new public library on Tuesday.',
    'Une ville côtière a inauguré une nouvelle bibliothèque publique mardi.',
    'A research team reported a new ocean mapping technique.',
    'Une équipe de recherche présente une nouvelle méthode de cartographie des océans.',
)


def _download():
    from huggingface_hub import snapshot_download
    return snapshot_download(repo_id=MODEL, revision=REVISION, allow_patterns=list(FILES),
                             token=False, max_workers=2)


def _encoder():
    import torch
    torch.set_num_threads(2)
    return E5Encoder(batch_size=4)


def _write(path, report):
    Path(path).write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')


def _status(exc):
    # Never print exception messages: provider bodies and paths can be sensitive.
    for item in (exc, getattr(exc, '__cause__', None), getattr(exc, '__context__', None)):
        response = getattr(item, 'response', None)
        code = getattr(response, 'status_code', None)
        if type(code) is int and 100 <= code <= 599: return code
    return None


def _benchmark(model):
    from .synthetic_validation import run
    return run(model)


def run(report_path='b3-e5-report.json', *, downloader=_download, encoder_factory=_encoder, benchmark_runner=_benchmark):
    report = {'status': 'failed', 'failure_stage': 'model_download', 'http_status': None,
              'model': MODEL, 'revision': REVISION, 'data_experiment': 'skipped_license_unconfirmed',
              'data_reason': 'Publisher rights for source articles have not been independently confirmed.'}
    _write(report_path, report)
    try:
        start = time.perf_counter()
        directory = Path(downloader())
        if directory.name != REVISION: raise ValueError('unexpected revision')
        required = ('config.json', 'model.safetensors', 'tokenizer_config.json')
        if any(not (directory / name).is_file() for name in required): raise ValueError('missing model file')
        if not any((directory / name).is_file() for name in ('tokenizer.json', 'sentencepiece.bpe.model')): raise ValueError('missing tokenizer')
        report['download_seconds'] = round(time.perf_counter() - start, 3)
        report['downloaded_bytes'] = sum((directory / name).stat().st_size for name in FILES if (directory / name).is_file())
        report['failure_stage'] = 'model_load'; _write(report_path, report)
        start = time.perf_counter(); model = encoder_factory()
        report['load_seconds'] = round(time.perf_counter() - start, 3)
        report['failure_stage'] = 'synthetic_inference'; _write(report_path, report)
        start = time.perf_counter(); vectors = model.encode(TITLES)
        report['inference_seconds'] = round(time.perf_counter() - start, 3)
        if len(vectors) != len(TITLES) or any(len(v) != 384 for v in vectors): raise ValueError('unexpected dimensions')
        norms = [math.sqrt(sum(x*x for x in v)) for v in vectors]
        if any(not math.isfinite(n) or abs(n - 1.0) > 1e-4 for n in norms): raise ValueError('invalid normalization')
        def dot(a, b): return sum(x*y for x, y in zip(vectors[a], vectors[b]))
        torch_module = getattr(model, 'torch', None)
        report.update(smoke_status='ok', failure_stage='synthetic_validation', dimensions=384, title_count=len(TITLES),
                      device='cpu', max_tokens=512, prefix='query:', pooling='attention_mask_mean_l2',
                      torch_version=str(torch_module.__version__) if torch_module is not None else None,
                      norm_min=round(min(norms), 6), norm_max=round(max(norms), 6),
                      paired_mean_cosine=round((dot(0, 1) + dot(2, 3)) / 2, 6),
                      unrelated_mean_cosine=round((dot(0, 2) + dot(1, 3)) / 2, 6),
                      peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        _write(report_path, report)
        report['synthetic_validation'] = benchmark_runner(model)
        report.update(status='ok', failure_stage=None,
                      peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        _write(report_path, report)
        return True
    except Exception as exc:
        report['http_status'] = _status(exc)
        report['error_category'] = 'http' if report['http_status'] is not None else 'unavailable_or_invalid'
        _write(report_path, report)
        return False


def main():
    if not run(): raise SystemExit('E5 experiment unavailable; see aggregate status artifact.')
    print('Synthetic E5 inference completed; see aggregate status artifact.')


if __name__ == '__main__': main()
