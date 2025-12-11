import React, { useState } from 'react';
import axios from 'axios';

const API_BASE = 'http://localhost:8000/api';

function VariablePoolCreate() {
    const [name, setName] = useState("");
    const [mode, setMode] = useState("no_replacement");
    const [itemsText, setItemsText] = useState("");
    const [message, setMessage] = useState("");

    const handleCreate = async () => {
        try {
            const items = itemsText.split('\n').filter(line => line.trim() !== "");
            const res = await axios.post(`${API_BASE}/variable-pools`, {
                name: name,
                mode: mode,
                items: items
            });
            setMessage(`Pool created! ID: ${res.data.id}`);
            setName("");
            setItemsText("");
        } catch (err) {
            setMessage("Error creating pool: " + (err.response?.data?.detail || err.message));
        }
    };

    return (
        <div className="admin-panel" aria-label="Create Variable Pool Section">
            <h3>Create Variable Pool</h3>
            <label>
                Pool Name:
                <input type="text" value={name} onChange={e => setName(e.target.value)} />
            </label><br/>

            <label>Mode: </label>
            <select value={mode} onChange={e => setMode(e.target.value)}>
                <option value="no_replacement">No Replacement</option>
                <option value="permutation">Permutation</option>
            </select><br/>

            <label>Items (one per line):</label><br/>
            <textarea
                rows="5"
                value={itemsText}
                onChange={e => setItemsText(e.target.value)}
                placeholder="red dress\nblue dress"
                style={{width: "100%"}}
            /><br/>

            <button onClick={handleCreate} disabled={!name || !itemsText}>Create Pool</button>
            <p>{message}</p>
        </div>
    );
}

export default VariablePoolCreate;
