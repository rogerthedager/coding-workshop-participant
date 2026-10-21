import { Link } from 'react-router-dom';
import { useDirectory } from '../directory';
import { timeAgo } from '../format';
import { Priority, SlaChip, StatusChip } from './ui';

/** The ticket table from the "My tickets" design, reused by search and queues. */
export default function IncidentTable({ items, showAssignee = true, assigneeFallback = 'Assigned' }) {
  const dir = useDirectory();
  return (
    <div className="scroll">
      <table>
        <thead>
          <tr>
            <th>Number</th><th>Title</th><th>Location</th><th>Status</th><th>Priority</th><th>SLA</th>
            {showAssignee && <th>Assignee</th>}
            <th>Updated</th>
          </tr>
        </thead>
        <tbody>
          {items.map((incident) => (
            <tr key={incident.id}>
              <td><Link className="mono" to={`/incidents/${incident.id}`}>{incident.number}</Link></td>
              <td className="t-title">
                {incident.title}
                {incident.following && <span className="tiny muted"> · following</span>}
              </td>
              <td className="muted nowrap">{dir.location(incident)}</td>
              <td><StatusChip status={incident.status} /></td>
              <td><Priority value={incident.priority} /></td>
              <td><SlaChip state={incident.sla_state} /></td>
              {showAssignee && (
                <td className="nowrap">{dir.person(incident.assigned_to, incident.people?.[incident.assigned_to] || assigneeFallback)}</td>
              )}
              <td className="muted nowrap">{timeAgo(incident.updated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
