import React, { useState, useEffect } from 'react';
import axios from 'axios';

const API_BASE = 'http://localhost:8000/api';

function TaskCreate() {
    const [workflows, setWorkflows] = useState([]);
    const [pools, setPools] = useState([]);

    const [selectedWorkflow, setSelectedWorkflow] = useState("");
    const [selectedPools, setSelectedPools] = useState([]);
    const [targetPrompts, setTargetPrompts] = useState(10);
    const [seedsPerPrompt, setSeedsPerPrompt] = useState(4);
    const [message, setMessage] = useState("");

    useEffect(() => {
        axios.get(`${API_BASE}/workflows`).then(res => setWorkflows(res.data));
        axios.get(`${API_BASE}/variable-pools`).then(res => setPools(res.data));
    }, []);

    const handleCreate = async () => {
        try {
            const res = await axios.post(`${API_BASE}/tasks`, {
                workflow_id: selectedWorkflow,
                variable_pool_ids: selectedPools,
                target_prompts: parseInt(targetPrompts),
                seeds_per_prompt: parseInt(seedsPerPrompt)
            });
            setMessage(`Task created! ID: ${res.data.id}`);
        } catch (err) {
            setMessage("Error creating task");
        }
    };

    const togglePool = (id) => {
        if (selectedPools.includes(id)) {
            setSelectedPools(selectedPools.filter(p => p !== id));
        } else {
            setSelectedPools([...selectedPools, id]);
        }
    };

    return (
        <div className="admin-panel">
            <h3>Create Task</h3>
            <label>Workflow:</label>
            <select onChange={e => setSelectedWorkflow(e.target.value)} value={selectedWorkflow}>
                <option value="">Select Workflow</option>
                {workflows.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>

            <div style={{margin: "10px 0"}}>
                <label>Variable Pools:</label>
                {pools.map(p => (
                    <div key={p.id}>
                        <input type="checkbox" checked={selectedPools.includes(p.id)} onChange={() => togglePool(p.id)} />
                        {p.name}
                    </div>
                ))}
            </div>

            <label>Target Prompts (K): </label>
            <input type="number" value={targetPrompts} onChange={e => setTargetPrompts(e.target.value)} /><br/>

            <label>Seeds per Prompt (N): </label>
            <input type="number" value={seedsPerPrompt} onChange={e => setSeedsPerPrompt(e.target.value)} /><br/>

            <button onClick={handleCreate} disabled={!selectedWorkflow || selectedPools.length === 0}>Create Task</button>
            <p>{message}</p>
        </div>
    );
}

export default TaskCreate;
