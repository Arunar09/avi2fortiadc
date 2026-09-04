/*
static/v_a_n_r_graph.js
D3.js Topology Visualization for V-A-N-R (VDOM-App-Network-Route)
*/

class VANRGraph {
    constructor(containerId, envName) {
        this.containerId = containerId;
        this.envName = envName;
        this.margin = {top: 20, right: 120, bottom: 20, left: 120};
        this.width = document.getElementById(containerId).clientWidth - this.margin.right - this.margin.left;
        this.height = 800 - this.margin.top - this.margin.bottom;
        
        this.svg = d3.select(`#${containerId}`)
            .append("svg")
            .attr("width", "100%")
            .attr("height", "100%")
            // Add zoom behavior
            .call(d3.zoom().on("zoom", (event) => {
                this.mainG.attr("transform", event.transform);
            }))
            .append("g");
            
        this.mainG = this.svg.append("g")
            .attr("transform", `translate(${this.margin.left},${this.margin.top})`);
            
        this.tree = d3.tree().nodeSize([40, 180]); // Use nodeSize for flexible panning
    }

    async refresh() {
        const resp = await fetch(`/api/topology/${this.envName}`);
        const data = await resp.json();
        
        // Clear previous
        this.mainG.selectAll("*").remove();
        
        if (!data || data.length === 0) {
            this.mainG.append("text")
                .attr("x", 0).attr("y", 0)
                .attr("text-anchor", "middle")
                .style("fill", "#666")
                .text("No topology data available. Define mappings in the Capture tab first.");
            return;
        }

        // Update Provenance Badge
        const badge = document.getElementById('topo-source-badge');
        if (badge) {
            const isForti = data.some(v => v.status === 'generated');
            badge.textContent = isForti ? "Verification (FortiADC JSON)" : "Simulation (Avi Source)";
            badge.className = isForti ? "badge badge-green" : "badge badge-blue";
        }

        // Wrap mapping in a virtual root
        const rootData = {
            name: "Environment Root",
            type: "environment",
            children: data
        };

        const root = d3.hierarchy(rootData);
        this.tree(root);

        // Links
        const link = this.mainG.selectAll(".link")
            .data(root.descendants().slice(1))
            .enter().append("path")
            .attr("class", "link")
            .attr("d", d => `
                M${d.y},${d.x}
                C${(d.y + d.parent.y) / 2},${d.x}
                 ${(d.y + d.parent.y) / 2},${d.parent.x}
                 ${d.parent.y},${d.parent.x}
            `)
            .style("fill", "none")
            .style("stroke", "#444")
            .style("stroke-width", "1.5px");

        // Nodes
        const node = this.mainG.selectAll(".node")
            .data(root.descendants())
            .enter().append("g")
            .attr("class", d => `node ${d.data.type}`)
            .attr("transform", d => `translate(${d.y},${d.x})`)
            .on("click", (event, d) => this.showStrategyPicker(d.data));

        node.append("circle")
            .attr("r", 5)
            .style("fill", d => this.getColor(d.data));

        node.append("text")
            .attr("dy", ".35em")
            .attr("x", d => d.children ? -13 : 13)
            .style("text-anchor", d => d.children ? "end" : "start")
            .text(d => d.data.name)
            .style("font-size", "11px")
            .style("fill", "#ccc");
    }

    getColor(d) {
        if (d.status === "conflict" || d.status === "orphaned") return "#f87171"; // Red
        if (d.status === "empty") return "#fb923c"; // Orange 
        
        switch(d.type) {
            case "vdom": return "#34d399"; // Green
            case "virtual_service": return "#60a5fa"; // Blue
            case "pool": return "#fbbf24"; // Amber
            case "net_group": return "#a78bfa"; // Purple
            default: return "#999";
        }
    }

    showStrategyPicker(data) {
        console.log("Picker for:", data);
        const event = new CustomEvent('vanr-node-select', { detail: data });
        document.dispatchEvent(event);
    }
}
