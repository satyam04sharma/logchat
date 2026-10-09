import './style.css';
export const metadata = {
  title: 'logchat — evidence for what changed',
  description: 'Ask your logs questions and inspect the evidence behind every answer.',
};

export default function Layout({ children }) {
  return <html lang="en"><body>{children}</body></html>;
}
