"""Bounded, deterministic conversational checkpoints. Never log evidence."""
import json
import re

MAX_CHARS = 4800
MAX_RECENT = 12
MAX_CONSTRAINTS = 16
MAX_TOPICS = 64
STOP = set('what which when where how why the and with from about earlier before again please that this those it did does happened changed evidence supports show logs memory topic question'.split())
STOP.update(('a an who is are was were be been being do have has had can could would should will '
             'of in on at to for without or they them these their explain describe compare return back '
             'previous same observed observation observations events event investigate problem '
             'failure failures failed error errors repeated services today yesterday current last past '
             'hour hours day days week weeks month months january february march april may june july '
             'august september october november december duration durations mean average latency latencies measured measurement measurements available availability').split())


def terms(text):
    return list(dict.fromkeys(word for word in re.findall(r'[a-z0-9_]{3,}', text.lower()) if word not in STOP and not word.isdecimal()))[:24]


def referring(text):
    # Bare temporal modifiers ("cache before October 2") are not chat references.
    return bool(re.search(r'\b(that|those|it|their|them|again|same|back to|return to)\b|'
                          r'\b(?:earlier|previous|before)\b(?!\s+(?:today|yesterday|\d|'
                          r'january|february|march|april|may|june|july|august|september|october|november|december|day\b))', text, re.I))


def empty():
    return {'turn_count': 0, 'recent': [], 'constraints': [], 'topics': [], 'scope': {}}


def advance(checkpoint, question, scope):
    value = {**{key: checkpoint[key] for key in empty()}, 'turn_count': checkpoint['turn_count'] + 1, 'scope': {key: scope.get(key) for key in ('environment_ids', 'timezone', 'start', 'end', 'compare_start', 'compare_end', 'service')}}
    snippet = question[:400]
    value['recent'] = [q for q in checkpoint['recent'] if q != snippet] + [snippet]
    value['recent'] = value['recent'][-MAX_RECENT:]
    constraints = re.findall(r'[^.!?]*(?:\bdo not\b|\bnever\b|\bonly\b|\bmust\b|\bkeep\b)[^.!?]*', question, re.I)
    value['constraints'] = list(dict.fromkeys(checkpoint['constraints'] + [q.strip()[:200] for q in constraints]))[-MAX_CONSTRAINTS:]
    value['topics'] = list(dict.fromkeys(checkpoint['topics'] + terms(question)))[-MAX_TOPICS:]
    # Scope and constraints take priority over recent snippets and topics.
    while len(json.dumps(value)) > MAX_CHARS and value['recent']:
        value['recent'].pop(0)
    while len(json.dumps(value)) > MAX_CHARS and value['topics']:
        value['topics'].pop(0)
    while len(json.dumps(value)) > MAX_CHARS and value['constraints']:
        value['constraints'].pop(0)
    return value


def read(connection, conversation_id, question=''):
    row = connection.execute('SELECT checkpoint FROM conversation_context WHERE conversation_id=?', (conversation_id,)).fetchone()
    value = json.loads(row['checkpoint']) if row else empty()
    if not row:
        # Lazy migration of existing investigations, bounded streaming memory.
        for item in connection.execute("SELECT content,request FROM conversation_messages WHERE conversation_id=? AND role='user' ORDER BY rowid", (conversation_id,)):
            request = json.loads(item['request']) if item['request'] else {}
            value = advance(value, item['content'], request.get('_effective', request))
    recalled = []
    if referring(question):
        query_terms = terms(question)[:6]
        if query_terms:
            expression = ' OR '.join('"' + term + '"' for term in query_terms)
            rows = connection.execute('''SELECT content FROM conversation_questions
                WHERE conversation_id=? AND conversation_questions MATCH ? ORDER BY rank LIMIT 4''',
                (conversation_id, expression)).fetchall()
            recalled = [row['content'][:400] for row in rows]
        elif not re.search(r'\b(?:\d+|january|february|march|april|may|june|july|august|september|october|november|december|today|yesterday)\b', question, re.I):
            # Skip prior anaphoric/metric-only turns, including after restart and
            # beyond the bounded reader page. No assistant text becomes evidence.
            for item in connection.execute("SELECT content FROM conversation_messages WHERE conversation_id=? AND role='user' ORDER BY rowid DESC", (conversation_id,)):
                if terms(item['content']):
                    recalled = [item['content'][:400]]
                    break
    return {**value, 'recalled': list(dict.fromkeys(recalled)), 'metadata': {
        'strategy': 'native_checkpoint_v1_user_only_lexical_recall', 'memory_is_evidence': False,
        'turn_count': value['turn_count'], 'checkpoint_chars': len(json.dumps(value)), 'max_checkpoint_chars': MAX_CHARS,
        'recent_limit': MAX_RECENT, 'constraint_limit': MAX_CONSTRAINTS, 'topic_limit': MAX_TOPICS,
        'recalled_count': len(recalled), 'model_context_max_chars': 24000,
        'limits': 'Lossy truncated user-only checkpoint; Concrete old-topic recall needs shared terms; metric-only references reuse the last concrete user topic. Assistant text is never evidence. Full transcript remains persisted; reader returns the latest 500 messages.'}}


def save(connection, conversation_id, question, scope):
    context = read(connection, conversation_id)
    # Called after inserting the turn: the initial lazy backfill already includes it.
    existing = connection.execute('SELECT 1 FROM conversation_context WHERE conversation_id=?', (conversation_id,)).fetchone()
    value = advance(context, question, scope) if existing else {k: context[k] for k in empty()}
    connection.execute('INSERT INTO conversation_context VALUES(?,?) ON CONFLICT(conversation_id) DO UPDATE SET checkpoint=excluded.checkpoint',
                       (conversation_id, json.dumps(value, sort_keys=True)))
