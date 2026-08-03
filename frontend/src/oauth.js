// Shared OAuth popup helper. Before this, PlatformCard.connect(), Schedule's
// openOAuth() and openDriveOAuth() each rolled their own window.open() —
// PlatformCard used '_blank' as the window name, so clicking "Conectar" twice
// opened two popups; the other two reused a fixed name but none of the three
// ever learned when the popup finished, so the parent screen never refreshed
// on its own after the user approved the connection.
//
// Usage: open the popup synchronously (so it isn't blocked by the browser's
// "no window.open() outside a click handler" rule), THEN resolve the auth URL
// and navigate it there:
//
//   const popup = openOAuthPopup(refresh)
//   const { auth_url } = await api.get(...)
//   navigateOAuthPopup(popup, auth_url)
export function openOAuthPopup(onClose, { width = 560, height = 680 } = {}) {
  const popup = window.open('', 'onda-oauth', `width=${width},height=${height}`)
  if (popup && onClose) {
    const timer = setInterval(() => {
      if (popup.closed) { clearInterval(timer); onClose() }
    }, 700)
  }
  return popup
}

export function navigateOAuthPopup(popup, url) {
  if (popup && !popup.closed) popup.location = url
  else window.location.href = url
}
