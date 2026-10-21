import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import { ErrorBox, Loading } from '../components/ui';
import { timeAgo } from '../format';
import { useLoad } from '../hooks';
import { Pager } from './MyTickets';

/** In-app notifications, newest first (FR-10). */
export default function Notifications() {
  const navigate = useNavigate();
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [error, setError] = useState(null);
  const list = useLoad(
    () => api.notifications({ unread: unreadOnly || undefined, page }),
    [unreadOnly, page],
  );
  const data = list.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  const open = async (item) => {
    setError(null);
    try {
      if (!item.read) await api.readNotification(item.id);
    } catch (err) {
      setError(err);
    }
    // A ticket the reader can no longer see shows the standard not-found page.
    navigate(`/incidents/${item.incident_id}`);
  };
  const readAll = async () => {
    setError(null);
    try {
      await api.readAllNotifications();
      list.reload();
    } catch (err) {
      setError(err);
    }
  };

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Notifications</h1>
          <p>Updates on tickets you reported, follow or work on.</p>
        </div>
        <div className="actions">
          <button type="button" className="btn" onClick={readAll}>Mark all as read</button>
        </div>
      </div>
      <ErrorBox error={error || list.error} />
      <section className="card">
        <div className="card-head">
          <div className="chips" role="group" aria-label="Show">
            {[[false, 'All'], [true, 'Unread']].map(([value, label]) => (
              <button key={label} type="button" className={`fchip${unreadOnly === value ? ' on' : ''}`}
                aria-pressed={unreadOnly === value} onClick={() => { setUnreadOnly(value); setPage(1); }}>
                {label}
              </button>
            ))}
          </div>
        </div>
        {list.loading && !data ? <Loading /> : data && (
          <>
            {data.items.map((item) => (
              <div key={item.id} className={`notif${item.read ? '' : ' unread'}`}>
                <div className="main">
                  <button type="button" className="linkbtn" style={{ textAlign: 'left', fontWeight: item.read ? 400 : 600 }}
                    onClick={() => open(item)}>
                    {item.message}
                  </button>
                  <div className="tiny muted" style={{ marginTop: 2 }}>
                    {item.read ? '' : 'Unread · '}{timeAgo(item.created_at)}
                  </div>
                </div>
              </div>
            ))}
            {data.items.length === 0 && (
              <div className="empty">{unreadOnly ? 'You are all caught up.' : 'No notifications yet.'}</div>
            )}
            <Pager data={data} page={page} pages={pages} setPage={setPage} />
          </>
        )}
      </section>
    </>
  );
}
