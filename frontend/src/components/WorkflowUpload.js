import React, { useState } from 'react';
import axios from 'axios';

const API_BASE = 'http://localhost:8000/api';

function WorkflowUpload() {
    const [name, setName] = useState("");
    const [version, setVersion] = useState("v1");
    const [batchSize, setBatchSize] = useState(4);
    const [file, setFile] = useState(null);
    const [message, setMessage] = useState("");

    const handleUpload = async (e) => {
        e.preventDefault();
        if (!file) return;

        const formData = new FormData();
        formData.append("name", name);
        formData.append("version", version);
        formData.append("max_batch_size", batchSize);
        formData.append("file", file);

        try {
            await axios.post(`${API_BASE}/workflows`, formData, {
                headers: { 'Content-Type': 'multipart/form-data' }
            });
            setMessage("Workflow uploaded successfully!");
        } catch (err) {
            setMessage("Upload failed: " + err.response?.data?.detail || err.message);
        }
    };

    return (
        <div className="admin-panel">
            <h3>Upload Workflow</h3>
            <form onSubmit={handleUpload}>
                <input type="text" placeholder="Name" value={name} onChange={e => setName(e.target.value)} required /><br/>
                <input type="text" placeholder="Version" value={version} onChange={e => setVersion(e.target.value)} required /><br/>
                <label>Max Batch Size: </label>
                <input type="number" value={batchSize} onChange={e => setBatchSize(e.target.value)} /><br/>
                <input type="file" onChange={e => setFile(e.target.files[0])} accept=".json" required /><br/>
                <button type="submit">Upload</button>
            </form>
            <p>{message}</p>
        </div>
    );
}

export default WorkflowUpload;
