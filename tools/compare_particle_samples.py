"""Create a local side-by-side review, never an automatic visual-parity verdict."""
import argparse,html,json,os
from pathlib import Path


def pairs(reference,preview):
    if reference.get('producer')!='l4d2-game':raise ValueError('Reference must be a game capture, not a simulator render')
    if preview.get('producer')!='hammerless-preview':raise ValueError('Expected preview report')
    global_gaps=[]
    for key in ('camera','width','height','framing'):
        if reference.get(key)!=preview.get(key):global_gaps.append('Mismatch: '+key)
    if reference.get('framing')!='fixed-perspective':global_gaps.append('Reference has no fixed perspective')
    result=[]
    for source in reference['samples']:
        match=next((s for s in preview['samples'] if s['effect']==source['effect'] and abs(s['seconds']-source['seconds'])<1e-6),None)
        gaps=list(global_gaps)
        if match is None:gaps.append('Missing matching preview sample')
        elif match.get('definition_sha256')!=source.get('definition_sha256') or not source.get('definition_sha256'):
            gaps.append('Particle definition fingerprint differs or is absent')
        result.append(dict(reference=source,preview=match,alignment_gaps=gaps,status='needs visual review'))
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--reference',required=True);p.add_argument('--preview',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();ref=Path(a.reference).resolve();pre=Path(a.preview).resolve();out=Path(a.output).resolve();out.mkdir(parents=True,exist_ok=True)
    result=pairs(json.loads(ref.read_text()),json.loads(pre.read_text()))
    def image(sample,base):
        if sample is None:return '<p>No matching image</p>'
        path=(base/sample['file']).resolve()
        if not path.is_relative_to(base):raise ValueError('Image path escapes report directory')
        if not path.exists():return '<p>Image file missing</p>'
        return '<img src="'+html.escape(os.path.relpath(path,out).replace('\\','/'),quote=True)+'">'
    body=['<!doctype html><meta charset="utf-8"><title>Particle reference review</title><style>body{background:#171717;color:#ddd;font:16px sans-serif;margin:24px}section{margin-bottom:32px}.pair{display:flex;gap:12px}.pair>div{width:48%}img{width:100%}pre{white-space:pre-wrap}</style>',
          '<h1>Particle reference review</h1><p>Left: L4D2. Right: independent preview. No automatic fidelity score. Timing is an interval; seeds, lighting and colour transfer may differ.</p>']
    for row in result:
        s=row['reference'];body.append('<section><h2>'+html.escape(s['effect'])+'</h2><p>Time: '+str(round(s['seconds'],3))+' seconds; capture interval '+html.escape(str(s.get('capture_interval')))+' </p>')
        body.append('<div class="pair"><div>'+image(s,ref.parent)+'</div><div>'+image(row['preview'],pre.parent)+'</div></div>')
        body.append('<pre>'+html.escape(json.dumps(dict(alignment_gaps=row['alignment_gaps'],preview_warnings=(row['preview'] or {}).get('runtime_warnings',[])),indent=2))+'</pre></section>')
    (out/'index.html').write_text(''.join(body),encoding='utf8');(out/'report.json').write_text(json.dumps(result,indent=2))
    print('Prepared',len(result),'comparison pairs:',out/'index.html')

if __name__=='__main__':main()
