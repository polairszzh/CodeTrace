import { useState } from "react";

function SearchBar({ onSearch }) {
    const [repoUrl, setRepoUrl] = useState('')
    const [filePath, setFilePath] = useState('')
    const [functionName, setFunctionName] = useState('')
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')

    const handleSearch = async () => {
        setLoading(true)
        setError('')
        try {
            const url = functionName
                ? `/api/trace/function?function_name=${encodeURIComponent(functionName)}`
                : '/api/trace'
            const res = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ repo_url: repoUrl, file_path: filePath }),
            })
            if (!res.ok) throw new Error('请求失败')
            const data = await res.json()
            onSearch(data)
        } catch (e) {
            setError(e.message)
        } finally {
            setLoading(false)
        }
    }

    return (
        <div style={{ padding: '20px', maxWidth: '600px', margin: '0 auto' }}>
            <div style={{ display: 'flex', gap: '10px', marginBottom: '10px' }}>
                <input
                    placeholder="GitHub 仓库 URL"
                    value={repoUrl}
                    onChange={(e) => setRepoUrl(e.target.value)}
                    style={{ flex: 1, padding: '8px' }}
                />
                <input
                    placeholder="文件路径"
                    value={filePath}
                    onChange={(e) => setFilePath(e.target.value)}
                    style={{ flex: 1, padding: '8px' }}
                />
                <input
                    placeholder="函数名 （可选，留空则追溯整个文件）"
                    value={functionName}
                    onChange={(e) => setFunctionName(e.target.value)}
                    style={{ flex: 1, padding: '8px' }}
                />
                <button onClick={handleSearch} disabled={loading}>
                    {loading ? '搜索中...' : '搜索'}
                </button>
            </div>
            {error && <div style={{ color: 'red' }}>{error}</div>}
        </div>
    )
}

export default SearchBar