"""Complete fixed-snapshot graph export; only the requested HTML touches disk."""
import argparse
import base64
import datetime
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlparse
from atlas import AtlasError, require, atlas_scope, ATLAS_PREFIX


def validate(data):
    require(isinstance(data, dict), 'Expected a graph object')
    require(isinstance(data.get('snapshot'), str) and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', data['snapshot']), 'Invalid snapshot')
    root = data.get('root', {})
    require(root.get('schema_version') == 1 and isinstance(root.get('vault_id'), str), 'Invalid graph root')
    nodes, edges = data.get('nodes'), data.get('edges')
    require(isinstance(nodes, list) and isinstance(edges, list), 'Nodes and edges required')
    ids = [n.get('id') for n in nodes]
    require(all(isinstance(i, str) and i for i in ids), 'Invalid node ID')
    id_set = set(ids)
    require(len(id_set) == len(ids), 'Duplicate node ID')
    require(len(nodes) == root.get('node_count'), 'Incomplete node inventory')
    require(sum(n.get('type') != 'domain' for n in nodes) == root.get('record_count'), 'Record count mismatch')
    require(sorted(n['id'] for n in nodes if n.get('type') == 'domain') == sorted(root.get('domains', [])), 'Domain inventory mismatch')
    require(len(edges) == root.get('edge_count'), 'Incomplete edge inventory')
    unique = set()
    for edge in edges:
        require(edge.get('from') in id_set and edge.get('to') in id_set and isinstance(edge.get('type'), str), 'Dangling or invalid edge')
        key = json.dumps(edge, sort_keys=True, ensure_ascii=False)
        require(key not in unique, 'Duplicate edge')
        unique.add(key)
    for node in nodes:
        for key in ('url',):
            url = node.get(key)
            if url:
                parsed = urlparse(url)
                require(parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment, 'Unsafe record URL')
    return data


def collect(root):
    root = atlas_scope(root)
    graph = json.loads(root['graph/root.json'])
    vault = json.loads(root['vault.json'])
    require(vault.get('schema_version') == 1 and vault.get('phase') != 'initializing'
            and vault.get('id') == graph.get('vault_id'), 'Vault identity mismatch')
    ids = []
    for path in graph['node_pages']:
        ids.extend(json.loads(root[path]))
    nodes, outgoing, incoming = [], [], []
    for rid in ids:
        node = json.loads(root['graph/nodes/' + rid.replace(':', '--') + '.json'])
        require(node['id'] == rid, 'Node ID mismatch')
        if node.get('path'):
            node['url'] = root.url(node['path'])
        nodes.append(node)
        for direction, target in [('outgoing', outgoing), ('incoming', incoming)]:
            for ref in node['adjacency'][direction]:
                page = json.loads(root[ref['path']])
                require(page['node'] == rid and page['direction'] == direction
                        and len(page['edges']) == ref['count'], 'Invalid adjacency page')
                require(all(e['from' if direction == 'outgoing' else 'to'] == rid for e in page['edges']), 'Wrong adjacency owner')
                target.extend(page['edges'])
    canonical = lambda edges: sorted(json.dumps(e, sort_keys=True, ensure_ascii=False) for e in edges)
    require(canonical(outgoing) == canonical(incoming), 'Incoming/outgoing indexes disagree')
    return validate({'snapshot': root.commit, 'base_path': ATLAS_PREFIX, 'root': graph, 'nodes': nodes, 'edges': outgoing,
                     'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                     'verification': 'graph_inventory'})


def render(data):
    validate(data)
    from atlas import time_key
    timed = sorted((n for n in data['nodes'] if n.get('temporal', {}).get('mode', 'none') != 'none'), key=time_key, reverse=True)
    data = {**data, 'timeline': [n['id'] for n in timed]}
    assets = Path(__file__).resolve().parents[1] / 'assets'
    template = (assets / 'atlas.html').read_text(encoding='utf-8')
    icons = json.loads((assets / 'feather-icons.json').read_text(encoding='utf-8'))
    icon_urls = {name: 'data:image/svg+xml;base64,' + base64.b64encode(svg.encode('utf-8')).decode('ascii')
                 for name, svg in icons.items()}
    license_text = (assets / 'feather-LICENSE.txt').read_text(encoding='utf-8')
    # Prevent HTML parser script termination; UI renders all source strings with textContent.
    encoded = json.dumps(data, ensure_ascii=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    # Replace template slots in one pass: a record may itself contain a slot name.
    slots = {'__ATLAS_DATA__': encoded,
             '__ATLAS_ICONS__': json.dumps(icon_urls),
             '__ATLAS_LICENSE__': license_text.replace('<', '&lt;')}
    return re.sub(r'__ATLAS_(?:DATA|ICONS|LICENSE)__', lambda match: slots[match[0]], template)


def export(data, output):
    path = Path(output).expanduser().resolve()
    require(path.suffix.lower() == '.html', 'Output must be an HTML file')
    require(not path.exists(), 'Output exists; choose a new filename')
    html = render(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(html)
    return {'status': 'html_exported', 'output': str(path), 'snapshot': data['snapshot'],
            'nodes': len(data['nodes']), 'edges': len(data['edges']), 'scope': 'complete_graph_with_summaries',
            'verification': data.get('verification', 'inventory_counts'), 'remote_modified': False,
            'ui': 'quiet-atlas-v2', 'themes': ['system', 'light', 'dark']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stdin', action='store_true', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(export(json.load(sys.stdin), args.output), ensure_ascii=False))
    except (AtlasError, ValueError, OSError) as error:
        print(json.dumps({'status': 'error', 'error': str(error)}, ensure_ascii=False))
        sys.exit(2)
