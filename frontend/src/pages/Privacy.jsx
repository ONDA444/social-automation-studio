export default function Privacy() {
  return (
    <div style={{ maxWidth: 760, margin: '0 auto', padding: '48px 24px', fontFamily: 'system-ui, sans-serif', lineHeight: 1.7, color: '#111' }}>
      <h1 style={{ fontSize: 28, fontWeight: 700, marginBottom: 4 }}>Privacy Policy</h1>
      <p style={{ color: '#555', marginBottom: 32 }}>Last updated: July 9, 2026</p>

      <p>Social Automation Studio ("we", "us", or "our") operates the SAS Automation platform, accessible at <strong>social-automation-studio.vercel.app</strong>. This Privacy Policy explains how we collect, use, and protect your information when you use our service.</p>

      <h2 style={{ fontSize: 20, fontWeight: 600, marginTop: 36, marginBottom: 8 }}>1. Information We Collect</h2>
      <p>We collect the following types of information:</p>
      <ul style={{ paddingLeft: 24 }}>
        <li><strong>Account credentials:</strong> OAuth tokens for YouTube, TikTok, Google Drive, and Instagram, used solely to publish content on your behalf.</li>
        <li><strong>Content data:</strong> Video files, titles, descriptions, and scheduling preferences you configure in the platform.</li>
        <li><strong>Usage data:</strong> Logs of jobs run, publication history, and system events, used for diagnostics and service improvement.</li>
      </ul>

      <h2 style={{ fontSize: 20, fontWeight: 600, marginTop: 36, marginBottom: 8 }}>2. How We Use Your Information</h2>
      <ul style={{ paddingLeft: 24 }}>
        <li>To authenticate and publish videos to your connected social media accounts.</li>
        <li>To schedule and manage your content publication calendar.</li>
        <li>To generate AI-assisted video titles, descriptions, and scripts on your behalf.</li>
        <li>To diagnose errors and improve service reliability.</li>
      </ul>
      <p>We do not sell, rent, or share your personal data with third parties for marketing purposes.</p>

      <h2 style={{ fontSize: 20, fontWeight: 600, marginTop: 36, marginBottom: 8 }}>3. Third-Party Services</h2>
      <p>Our platform integrates with the following third-party services. Your use of these integrations is subject to their respective privacy policies:</p>
      <ul style={{ paddingLeft: 24 }}>
        <li><a href="https://policies.google.com/privacy" target="_blank" rel="noreferrer">Google / YouTube Privacy Policy</a></li>
        <li><a href="https://www.tiktok.com/legal/privacy-policy" target="_blank" rel="noreferrer">TikTok Privacy Policy</a></li>
        <li><a href="https://www.facebook.com/privacy/policy/" target="_blank" rel="noreferrer">Meta / Instagram Privacy Policy</a></li>
      </ul>
      <p>OAuth tokens are encrypted at rest using AES-256 encryption and are never exposed in plaintext outside the server environment.</p>

      <h2 style={{ fontSize: 20, fontWeight: 600, marginTop: 36, marginBottom: 8 }}>4. Data Retention</h2>
      <p>We retain your data for as long as your account is active. You may request deletion of your account and all associated data at any time by contacting us. Upon deletion, OAuth tokens and personal data are permanently removed within 30 days.</p>

      <h2 style={{ fontSize: 20, fontWeight: 600, marginTop: 36, marginBottom: 8 }}>5. Data Security</h2>
      <p>We implement industry-standard security measures including HTTPS-only communication, encrypted token storage, and access controls. However, no system is 100% secure and we cannot guarantee absolute security.</p>

      <h2 style={{ fontSize: 20, fontWeight: 600, marginTop: 36, marginBottom: 8 }}>6. Your Rights</h2>
      <p>You have the right to:</p>
      <ul style={{ paddingLeft: 24 }}>
        <li>Access the personal data we hold about you.</li>
        <li>Correct inaccurate data.</li>
        <li>Request deletion of your data.</li>
        <li>Revoke OAuth access at any time through the respective platform's account settings.</li>
      </ul>

      <h2 style={{ fontSize: 20, fontWeight: 600, marginTop: 36, marginBottom: 8 }}>7. Children's Privacy</h2>
      <p>Our service is not directed to individuals under the age of 13. We do not knowingly collect personal information from children.</p>

      <h2 style={{ fontSize: 20, fontWeight: 600, marginTop: 36, marginBottom: 8 }}>8. Changes to This Policy</h2>
      <p>We may update this Privacy Policy from time to time. We will notify users of material changes by updating the "Last updated" date above.</p>

      <h2 style={{ fontSize: 20, fontWeight: 600, marginTop: 36, marginBottom: 8 }}>9. Contact Us</h2>
      <p>If you have any questions about this Privacy Policy, please contact us at: <a href="mailto:orionreidas@proton.me">orionreidas@proton.me</a></p>

      <p style={{ marginTop: 48, color: '#888', fontSize: 13 }}>© 2026 Social Automation Studio. All rights reserved.</p>
    </div>
  )
}
