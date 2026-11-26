export function historyDocuments(chats) {
  const documents = [];
  let remaining = 4_000_000;
  for (const chat of [...chats].sort((a,b)=>b.updated-a.updated)) {
    if (!chat.remote && !chat.messages.length) continue;
    documents.push({id: chat.id + ':title', chatId: chat.id, title: chat.title, index: -1, text: ''});
    for (let index=chat.messages.length-1; index>=0 && remaining>0 && documents.length<3000; index--) {
      const message=chat.messages[index];
      if (message.pending || message.failed || !message.content) continue;
      const text=String(message.content).slice(0, Math.min(16000,remaining));
      remaining-=text.length;
      documents.push({id: chat.id + ':' + index, chatId: chat.id, serverId: message.serverId, title:'', index, text});
    }
  }
  return documents;
}

export function searchHistory(MiniSearch, chats, input) {
  const engine = new MiniSearch({
    fields: ['title', 'text'],
    storeFields: ['chatId', 'title', 'text', 'index', 'serverId'],
    searchOptions: {boost: {title: 3}, combineWith: 'AND', prefix: true},
  });
  engine.addAll(historyDocuments(chats));
  const query = input.slice(0, 200).trim().toLocaleLowerCase();
  return engine.search(query).slice(0, 35).map(({title, text, ...result}) => {
    const word = query.split(/\s+/u)[0];
    const offset = Math.max(0, text.toLocaleLowerCase().indexOf(word) - 32);
    const snippet = text.replace(/\s+/gu, ' ').slice(offset, offset + 125);
    return {...result, title, snippet: (offset ? '…' : '') + snippet + (text.length > offset + 125 ? '…' : '')};
  });
}
