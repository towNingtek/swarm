// DSH 0.2 requires a Peer on every in-process gateway call (0.1.7 used the operator by default); without
// one the call fails reading `request.peer.signal` and the board answered HTTP 502. The plugin acts for the
// site owner, so it calls as the owner Peer DSH 0.2 exposes as `connection.browserPeer`; on 0.1.7 there is
// none and requests go through unchanged. Same fix as dsh-share-room 0.3.3.

/**
 * The gateway, with `peer` added to every invoke/stream request that has none.
 * peerOf() is read per call (the connection service can be replaced); undefined means "no peer needed".
 */
export function withOwnerPeer(gateway, peerOf) {
  if (!gateway) return gateway
  const add = (request) => {
    if (!request || typeof request !== 'object' || request.peer !== undefined) return request
    const peer = peerOf()
    return peer === undefined ? request : { ...request, peer }
  }
  return {
    invoke: (request) => gateway.invoke(add(request)),
    stream: (request) => gateway.stream(add(request)),
  }
}
