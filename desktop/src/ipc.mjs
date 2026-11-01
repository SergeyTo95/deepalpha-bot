/** Accept IPC only from the owned file page's main frame. */
export function ownedPage(event, contents, pageURL) {
  return event.sender === contents && event.senderFrame === contents.mainFrame
    && event.senderFrame.url === pageURL;
}
