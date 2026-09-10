"""JSON-RPC app-server fixture exercising real host dynamic-tool callbacks."""
import json
import re
import sys
import uuid

thread_id = uuid.uuid4().hex
active_turn = None
pending = []
request_id = 10000


def emit(value):
    print(json.dumps(value), flush=True)


def completed(text):
    emit({'method':'item/completed','params':{'threadId':thread_id,'turnId':active_turn,
          'item':{'type':'agentMessage','id':'answer','text':text}}})
    emit({'method':'turn/completed','params':{'threadId':thread_id,'turn':{'id':active_turn,'status':'completed'}}})


def next_tool():
    global request_id
    if not pending:
        completed('Proposal submitted')
        return
    name, args = pending.pop(0)
    request_id += 1
    emit({'id':request_id,'method':'item/tool/call','params':{'threadId':thread_id,'turnId':active_turn,
          'callId':str(request_id),'tool':name,'arguments':args}})


for line in sys.stdin:
    message = json.loads(line)
    if 'method' not in message:
        if message.get('id') == request_id:
            if not message.get('result',{}).get('success'):
                raise RuntimeError('Host rejected fixture tool: ' + str(message))
            next_tool()
        continue
    if 'id' not in message:
        continue
    method, params = message['method'], message.get('params',{})
    result = {}
    if method == 'config/read':
        result = {'config':{}}
    elif method == 'thread/start':
        result = {'thread':{'id':thread_id}}
    elif method == 'turn/start':
        active_turn = str(message['id'])
        result = {'turn':{'id':active_turn}}
    emit({'id':message['id'],'result':result})
    if method != 'turn/start':
        continue
    if params.get('outputSchema'):
        completed(json.dumps({'create_patterns':[{'name':'build.md','content':'Use the configured JDK.'}],
          'update_patterns':[],'update_index':'[build](wiki/patterns/build.md): Use configured JDK','append_log':'Recorded build lesson'}))
    else:
        prompt = params['input'][0]['text']
        catalog = json.loads(prompt.split('\nCatalog:\n')[1].split('\nResources:\n')[0])
        name = re.search(r'only authorized Skill name is ([a-z0-9-]+)',prompt).group(1)
        pending = [('read_file',{'path':p['path']}) for p in catalog]
        pending.append(('finish',{'action':'create','name':name,
          'skill_md':f'---\nname: {name}\ndescription: Build project\n---\nUse the configured JDK.',
          'purpose_md':'# Origin\nBuild pattern'}))
        next_tool()
