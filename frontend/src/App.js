import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { BrowserRouter as Router, Route, Routes, Link, useParams } from 'react-router-dom';
import './App.css';

const API_BASE = 'http://localhost:8000/api';

// --- Components ---

function WorkerList() {
  const [workers, setWorkers] = useState([]);

  useEffect(() => {
    fetchWorkers();
  }, []);

  const fetchWorkers = async () => {
    try {
      const res = await axios.get(`${API_BASE}/workers`);
      setWorkers(res.data);
    } catch (err) {
      console.error(err);
    }
  };

  const testWorker = async (id) => {
    try {
      const res = await axios.post(`${API_BASE}/workers/${id}/test`);
      alert(`Worker Health: ${res.data.healthy ? 'OK' : 'FAIL'}`);
      fetchWorkers();
    } catch (err) {
      alert('Error testing worker');
    }
  };

  return (
    <div className="worker-list">
      <h2>ComfyUI Workers</h2>
      <ul>
        {workers.map(w => (
          <li key={w.id} className={w.status === 'HEALTHY' ? 'healthy' : 'unhealthy'}>
            <strong>{w.name}</strong> ({w.base_url}) - {w.status}
            <button onClick={() => testWorker(w.id)}>Test Connection</button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function TaskList() {
    // Placeholder for Task Management UI
    // In a real app, this would list tasks.
    // For MVP testing, we allow navigating to annotation by Task ID manually or listing simple links if we had tasks.
    const [taskId, setTaskId] = useState("");

    return (
        <div className="task-list">
            <h2>Task Management</h2>
            <div className="task-input">
                <input
                    type="text"
                    placeholder="Enter Task ID to Annotate"
                    value={taskId}
                    onChange={e => setTaskId(e.target.value)}
                />
                <Link to={`/annotate/${taskId}`}>
                    <button disabled={!taskId}>Go to Annotation Workbench</button>
                </Link>
            </div>
            <p>(Create tasks via API for now)</p>
        </div>
    );
}

function AnnotationWorkbench() {
  const { taskId } = useParams();
  const [batch, setBatch] = useState(null);
  const [loading, setLoading] = useState(false);

  const fetchNextBatch = async () => {
    setLoading(true);
    try {
      // Simple cursor-based fetch (fetching 1 batch at a time for workbench)
      const res = await axios.get(`${API_BASE}/tasks/${taskId}/batches?limit=1`);
      if (res.data.length > 0) {
        setBatch(res.data[0]);
      } else {
        alert("No more batches to annotate!");
        setBatch(null);
      }
    } catch (err) {
      console.error(err);
    } finally {
        setLoading(false);
    }
  };

  useEffect(() => {
    if (taskId) fetchNextBatch();
  }, [taskId]);

  const [selectedRejected, setSelectedRejected] = useState(null);

  const submitAnnotation = async (chosenIndex, rejectedIndex, spam) => {
      if (!batch) return;
      try {
          await axios.post(`${API_BASE}/annotations`, {
              task_id: taskId,
              batch_id: batch.batch_id,
              chosen_index: chosenIndex,
              rejected_index: rejectedIndex, // Can be null if only choosing best
              spam: spam
          });
          setSelectedRejected(null); // Reset
          fetchNextBatch();
      } catch (err) {
          alert("Failed to submit annotation");
      }
  };

  if (loading) return <div>Loading next batch...</div>;
  if (!batch) return <div>No batch loaded.</div>;

  return (
    <div className="annotation-workbench">
      <h3>Prompt: {batch.prompt_text}</h3>
      <p style={{fontSize: "0.8em"}}>Tip: Long press/Double click to mark as Rejected (Red border)</p>

      <div className="gallery">
          {batch.thumbnails.map((url, idx) => (
              <div key={idx} className={`image-card ${selectedRejected === idx ? 'rejected' : ''}`}>
                  <img src={url} alt={`Seed ${batch.seeds[idx]}`} />
                  <div className="actions">
                    <button className="choose-btn" onClick={() => submitAnnotation(idx, selectedRejected === idx ? null : selectedRejected, false)}>
                        Choose Best
                    </button>
                    <button className="reject-btn" onClick={() => setSelectedRejected(idx === selectedRejected ? null : idx)}>
                        {selectedRejected === idx ? "Un-reject" : "Mark Worst"}
                    </button>
                  </div>
              </div>
          ))}
      </div>
      <button onClick={() => submitAnnotation(-1, -1, true)} className="spam-btn">Mark All as Spam</button>
    </div>
  );
}

function App() {
  return (
    <Router>
      <div className="App">
        <header className="App-header">
          <h1>ComfyUI Data Labeling</h1>
          <nav>
            <Link to="/">Workers</Link> | <Link to="/tasks">Tasks</Link>
          </nav>
        </header>
        <Routes>
          <Route path="/" element={<WorkerList />} />
          <Route path="/tasks" element={<TaskList />} />
          <Route path="/annotate/:taskId" element={<AnnotationWorkbench />} />
        </Routes>
      </div>
    </Router>
  );
}

export default App;
