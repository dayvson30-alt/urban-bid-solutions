"""
Coletor diario do Urban Bid Solutions (roda no GitHub Actions).

  python collector/collect.py winners   # USASpending.gov -> winners_history.json (sem chave)
  python collector/collect.py sam       # SAM.gov -> market_intel.json, deserted_bids.json, bids.json (SAM_API_KEY)
  python collector/collect.py all

So escreve arquivos locais; o workflow faz o commit.
SAM.gov limita chaves nao-federais a poucas chamadas/dia: este script usa no maximo SAM_MAX_PAGES (3).
"""
import json, os, sys, statistics, time, urllib.request, urllib.parse, urllib.error
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime.now(timezone.utc)
TODAY = NOW.date()
UA = {'User-Agent': 'UrbanBidSolutions/2.0 (urbancleanrepair@gmail.com)'}

NAICS_SECTORS = {
    '11': 'Agricultura & Florestas', '21': 'Mineracao & Gas', '22': 'Utilities',
    '23': 'Construcao Civil', '31': 'Alimentos & Bebidas', '32': 'Papel, Quimicos, Plasticos',
    '33': 'Metais, Maquinas, Eletronicos', '42': 'Comercio Atacado',
    '44': 'Varejo (Lojas Fisicas)', '45': 'Varejo Online & Correios',
    '48': 'Transporte Rodoviario/Ferroviario', '49': 'Armazenagem & Courier',
    '51': 'Tecnologia & Comunicacao', '52': 'Financas & Seguros',
    '53': 'Imoveis & Aluguel', '54': 'Servicos Profissionais & Tecnicos',
    '55': 'Gestao Empresarial', '56': 'Servicos Administrativos & Limpeza',
    '61': 'Educacao', '62': 'Saude & Assistencia Social',
    '71': 'Arte, Entretenimento & Esportes', '72': 'Alimentacao & Hotelaria',
    '81': 'Outros Servicos (Reparo, Lavanderia, etc.)', '92': 'Administracao Publica',
}


def http_json(url, payload=None, timeout=60):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = dict(UA)
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method='POST' if data else 'GET')
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def load(name, default=None):
    try:
        with open(os.path.join(ROOT, name), encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(name, obj):
    with open(os.path.join(ROOT, name), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(f'  -> {name}')


def fmt_br(v, cents=True):
    """$1.234.567,89 — mesmo formato que o painel ja le."""
    s = f'{v:,.2f}' if cents else f'{v:,.0f}'
    return '$' + s.replace(',', '#').replace('.', ',').replace('#', '.')


def parse_date(s):
    if not s:
        return None
    s = str(s)[:10]
    for f in ('%Y-%m-%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


# ── USASpending: quem ganhou contratos parecidos ─────────────────────────────
WINNER_GROUPS = {
    'cleaning':     {'codes': ['561720', '561740', '561790'], 'label': 'Limpeza Predial & Janitorial', 'icon': '\U0001F9F9'},
    'landscaping':  {'codes': ['561730'],                     'label': 'Landscaping & Corte de Grama', 'icon': '\U0001F33F'},
    'construction': {'codes': ['236220', '238910'],           'label': 'Construcao & Manutencao',      'icon': '\U0001F528'},
}


def usaspending_awards(codes, start, pages=3):
    out = []
    for page in range(1, pages + 1):
        resp = http_json('https://api.usaspending.gov/api/v2/search/spending_by_award/', {
            'filters': {
                'place_of_performance_locations': [{'country': 'USA', 'state': 'FL'}],
                'time_period': [{'start_date': start, 'end_date': TODAY.isoformat()}],
                'award_type_codes': ['A', 'B', 'C', 'D'],
                'naics_codes': codes,
            },
            'fields': ['Award ID', 'Recipient Name', 'Start Date', 'End Date', 'Award Amount',
                       'Description', 'awarding_sub_agency_name', 'generated_internal_id'],
            # mais recentes primeiro: ordenar por valor enviesava a mediana para os contratos gigantes
            'sort': 'Start Date', 'order': 'desc', 'limit': 100, 'page': page,
        })
        out += resp.get('results', [])
        if not resp.get('page_metadata', {}).get('hasNext'):
            break
        time.sleep(1)
    return [a for a in out if (a.get('Award Amount') or 0) > 0]


def collect_winners():
    print('USASpending -> winners_history.json')
    start = (TODAY - timedelta(days=5 * 365)).isoformat()
    groups, index = {}, {}
    for gid, g in WINNER_GROUPS.items():
        awards = usaspending_awards(g['codes'], start)
        print(f'  {gid}: {len(awards)} contratos')
        vendors = {}
        for a in awards:
            name = (a.get('Recipient Name') or 'N/A').strip()
            v = vendors.setdefault(name, {'name': name, 'totalValue': 0.0, 'contractCount': 0, 'contracts': []})
            amt = float(a['Award Amount'])
            v['totalValue'] += amt
            v['contractCount'] += 1
            v['contracts'].append({
                'description': (a.get('Description') or '')[:120],
                'amount': amt, 'amountFmt': fmt_br(amt),
                'startDate': a.get('Start Date') or '', 'endDate': a.get('End Date') or '',
                'agency': a.get('awarding_sub_agency_name') or '',
                'link': f"https://www.usaspending.gov/award/{a.get('generated_internal_id', '')}",
            })
        top = sorted(vendors.values(), key=lambda v: -v['totalValue'])[:8]
        for v in top:
            v['avgValue'] = v['totalValue'] / v['contractCount']
            v['totalFmt'] = fmt_br(v['totalValue'])
            v['avgValueFmt'] = fmt_br(v['avgValue'], cents=False)
            v['contracts'] = sorted(v['contracts'], key=lambda c: c['startDate'], reverse=True)[:5]
        amounts = [float(a['Award Amount']) for a in awards]
        med = statistics.median(amounts) if amounts else 0
        groups[gid] = {
            'codes': g['codes'], 'label': g['label'], 'icon': g['icon'],
            'topVendors': top, 'totalContracts': len(awards),
            'priceMedian': med, 'priceMedianFmt': fmt_br(med),
            'priceMinFmt': fmt_br(min(amounts)) if amounts else 'N/A',
            'priceMaxFmt': fmt_br(max(amounts)) if amounts else 'N/A',
            'winStrategy': (f'Para competir: fique abaixo de {fmt_br(med)} (mediana). '
                            f'Alvo: {fmt_br(med * 0.9, cents=False)} (90%).') if amounts else '',
        }
        for c in g['codes']:
            index[c] = gid
        time.sleep(1)
    save('winners_history.json', {
        'lastUpdated': NOW.isoformat(), 'source': 'USASpending.gov', 'state': 'FL',
        'period': f'{start} a {TODAY.isoformat()}',
        'byNaicsGroup': groups, 'naicsIndex': index,
    })


# ── SAM.gov: editais federais em FL ─────────────────────────────────────────
SOLICIT_TYPES = ('Solicitation', 'Combined Synopsis/Solicitation')
OPEN_TYPES = SOLICIT_TYPES + ('Presolicitation', 'Sources Sought', 'Special Notice')
# NAICS que a Urban Clean atende (prefixos)
RELEVANT_NAICS = ('5617', '561210', '2383', '238990', '424130', '325612')


def nicho_for(naics):
    if naics.startswith('561730'):
        return 'landscaping'
    if naics.startswith(('5617', '561210')):
        return 'cleaning'
    if naics.startswith(('23',)):
        return 'construction'
    return 'products'


def sam_fetch(key, days, max_pages):
    fixture = os.environ.get('SAM_FIXTURE')
    if fixture:  # teste local sem gastar chamadas
        with open(fixture, encoding='utf-8') as f:
            return json.load(f)['opportunitiesData']
    out, offset = [], 0
    for _ in range(max_pages):
        q = urllib.parse.urlencode({
            'api_key': key, 'state': 'FL', 'limit': 1000, 'offset': offset,
            'postedFrom': (TODAY - timedelta(days=days)).strftime('%m/%d/%Y'),
            'postedTo': TODAY.strftime('%m/%d/%Y'),
        })
        resp = http_json('https://api.sam.gov/opportunities/v2/search?' + q)
        batch = resp.get('opportunitiesData', [])
        out += batch
        total = resp.get('totalRecords', 0)
        print(f'  SAM pagina {offset}: {len(batch)} (total {total})')
        offset += 1
        if len(out) >= total or not batch:
            break
    return out


def agency_of(o):
    path = (o.get('fullParentPathName') or '').split('.')
    return path[-1].strip() if path and path[-1] else (o.get('organizationName') or 'N/A')


def opp_link(o):
    return f"https://sam.gov/opp/{o.get('noticeId')}/view"


def collect_sam():
    key = os.environ.get('SAM_API_KEY', '')
    if not key and not os.environ.get('SAM_FIXTURE'):
        print('SAM.gov: SAM_API_KEY nao definida — pulando (Mercado, Re-Bid e bids federais ficam como estao)')
        return
    print('SAM.gov -> market_intel.json, deserted_bids.json, bids.json')
    opps = sam_fetch(key, days=90, max_pages=int(os.environ.get('SAM_MAX_PAGES', '3')))
    if not opps:
        print('  SAM.gov sem resultados — nada alterado')
        return
    for o in opps:
        o['_naics'] = str(o.get('naicsCode') or '00')
        o['_posted'] = parse_date(o.get('postedDate'))
        o['_deadline'] = parse_date(o.get('responseDeadLine'))
    build_market(opps)
    build_deserted(opps)
    merge_bids(opps)


def build_market(opps):
    start = TODAY - timedelta(days=30)
    recent = [o for o in opps if o['_posted'] and o['_posted'] >= start and o.get('type') in OPEN_TYPES
              and (o['_deadline'] is None or o['_deadline'] >= TODAY)]
    sectors = {}
    for o in recent:
        sec = o['_naics'][:2]
        s = sectors.setdefault(sec, {'sector': sec, 'sectorDesc': NAICS_SECTORS.get(sec, 'Outros'),
                                     'count': 0, 'naicsCodes': {}, 'samples': [], 'totalValue': 0, 'agencies': []})
        s['count'] += 1
        s['naicsCodes'][o['_naics']] = s['naicsCodes'].get(o['_naics'], 0) + 1
        ag = agency_of(o)
        if ag not in s['agencies']:
            s['agencies'].append(ag)
        if len(s['samples']) < 5:
            s['samples'].append({'title': o.get('title', ''), 'naics': o['_naics'], 'type': o.get('type', ''),
                                 'agency': ag, 'deadline': o['_deadline'].isoformat() if o['_deadline'] else '',
                                 'setAside': o.get('typeOfSetAsideDescription') or '', 'link': opp_link(o)})
    for s in sectors.values():
        s['naicsTop'] = [{'code': c, 'count': n} for c, n in sorted(s['naicsCodes'].items(), key=lambda x: -x[1])[:5]]
        s['agencies'] = s['agencies'][:10]
    raw_fields = ('noticeId', 'title', 'solicitationNumber', 'fullParentPathName', 'postedDate', 'type',
                  'baseType', 'archiveDate', 'typeOfSetAsideDescription', 'typeOfSetAside', 'responseDeadLine', 'naicsCode')
    save('market_intel.json', {
        'lastUpdated': NOW.isoformat(), 'source': 'SAM.gov', 'state': 'FL', 'totalBids': len(recent),
        'dateRange': {'from': start.strftime('%m/%d/%Y'), 'to': TODAY.strftime('%m/%d/%Y')},
        'sectors': sorted(sectors.values(), key=lambda s: -s['count']),
        'rawBids': [{k: o.get(k) for k in raw_fields} for o in recent],
    })


def build_deserted(opps):
    """Editais do nosso nicho cujo prazo passou e nao ha Award Notice publicado com o mesmo numero."""
    awarded = {o.get('solicitationNumber') for o in opps if o.get('type') == 'Award Notice' and o.get('solicitationNumber')}
    closed = [o for o in opps if o['_naics'].startswith(RELEVANT_NAICS) and o.get('type') in SOLICIT_TYPES
              and o['_deadline'] and o['_deadline'] < TODAY]
    out = []
    for o in closed:
        if o.get('solicitationNumber') in awarded:
            continue
        days = (TODAY - o['_deadline']).days
        urg = 'ALTA' if days <= 30 else 'MEDIA' if days <= 60 else 'BAIXA'
        out.append({
            'id': f"deserted-{o.get('noticeId')}", 'source': 'SAM.gov Federal', 'noticeId': o.get('noticeId'),
            'bidNum': o.get('solicitationNumber') or f"SAM-{str(o.get('noticeId'))[:10]}",
            'solicitationNumber': o.get('solicitationNumber') or '', 'bidType': o.get('type'),
            'title': (o.get('title') or '').strip(), 'nicho': nicho_for(o['_naics']), 'naics': o['_naics'],
            'agency': agency_of(o), 'postedDate': o['_posted'].isoformat() if o['_posted'] else '',
            'deadline': o['_deadline'].isoformat(), 'daysClosed': days,
            'reason': f'Prazo encerrado ha {days}d sem award publicado',
            'setAside': o.get('typeOfSetAsideDescription') or '',
            'strategicValue': urg, 'urgency': urg,
            'recommendedAction': 'Contatar o procurement officer e perguntar se o edital sera reaberto. Registrar interesse.',
            'contact': ((o.get('pointOfContact') or [{}])[0] or {}).get('email', ''),
            'link': opp_link(o),
        })
    out.sort(key=lambda d: d['daysClosed'])
    save('deserted_bids.json', {
        'lastUpdated': NOW.isoformat(), 'source': 'SAM.gov',
        'note': 'Editais federais em FL (NAICS do nosso nicho) com prazo encerrado e sem Award Notice publicado nos ultimos 90 dias.',
        'totalArchived': len(closed), 'totalDeserted': len(out), 'desertedBids': out,
    })


def merge_bids(opps):
    new = {}
    for o in opps:
        if not o['_naics'].startswith(RELEVANT_NAICS) or o.get('type') not in SOLICIT_TYPES:
            continue
        if not o['_deadline'] or o['_deadline'] < TODAY:
            continue
        nicho = nicho_for(o['_naics'])
        pop = o.get('placeOfPerformance') or {}
        poc = (o.get('pointOfContact') or [{}])[0] or {}
        bid_id = f"sam-{o.get('noticeId')}"
        new[bid_id] = {
            'id': bid_id, 'source': 'SAM.gov Federal', 'orgao': agency_of(o),
            'condado': ((pop.get('city') or {}).get('name') or ''), 'estado': 'FL',
            'bidNum': o.get('solicitationNumber') or f"SAM-{str(o.get('noticeId'))[:12]}",
            'solicitationNumber': o.get('solicitationNumber') or '',
            'bidType': o.get('type'), 'title': (o.get('title') or '').strip(),
            'nicho': nicho, 'model': 'product' if nicho == 'products' else 'service',
            'openDate': o['_posted'].isoformat() if o['_posted'] else '',
            'deadline': o['_deadline'].isoformat(), 'prazo_submissao': o['_deadline'].isoformat(),
            'link': opp_link(o), 'pdf_url': '', 'documents': [], 'pdf_links': [],
            'resourceLinks': o.get('resourceLinks') or [],
            'veredito': 'watch', 'fit_score': 50,
            'set_aside': o.get('typeOfSetAsideDescription') or '', 'naics': o['_naics'],
            'contact': poc.get('email', ''), 'contactName': poc.get('fullName', ''),
            'escopo': f"SAM.gov. NAICS: {o['_naics']}. Orgao: {o.get('fullParentPathName', '')}",
            'fetchedAt': TODAY.isoformat(),
        }
    data = load('bids.json', {'bids': []})
    kept = []
    for b in data.get('bids', []):
        if b.get('id') in new:
            new[b['id']] = {**b, **new[b['id']]}  # preserva campos de analise (ai_analysis etc.)
        elif b.get('source') == 'SAM.gov Federal':
            d = parse_date(b.get('deadline'))
            if d and d >= TODAY:
                kept.append(b)  # saiu da janela da busca mas ainda esta aberto
        else:
            kept.append(b)  # Polk / MFMP / Orange: so atualizados manualmente por enquanto
    bids = kept + list(new.values())
    summary = {k: sum(1 for b in bids if b.get('veredito') == k) for k in ('go', 'watch', 'wait', 'skip')}
    data.update({'lastUpdated': NOW.isoformat(), 'updatedAt': NOW.isoformat(), 'summary': summary, 'bids': bids})
    print(f'  bids.json: {len(new)} SAM abertos no nicho, {len(bids)} no total')
    save('bids.json', data)


if __name__ == '__main__':
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    failed = False
    for name, fn in (('winners', collect_winners), ('sam', collect_sam)):
        if what in (name, 'all'):
            try:
                fn()
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as e:
                print(f'ERRO em {name}: {e}')
                failed = True
    sys.exit(1 if failed else 0)
