document.addEventListener("DOMContentLoaded", function () {
    var targets = document.querySelectorAll(".section-card, .metric-card, .hero-panel");
    targets.forEach(function (el) {
        el.classList.add("reveal");
    });

    var observer = new IntersectionObserver(
        function (entries) {
            entries.forEach(function (entry) {
                if (entry.isIntersecting) {
                    entry.target.classList.add("is-visible");
                    observer.unobserve(entry.target);
                }
            });
        },
        { threshold: 0.08 }
    );

    targets.forEach(function (el) {
        observer.observe(el);
    });

    initExecutionWidget();
    initTestTreeDnD();
});

function initTestTreeDnD() {
    var tree = document.getElementById("testTree");
    if (!tree || tree.dataset.canManage !== "1") {
        return;
    }

    var moveUrl = tree.dataset.treeMoveUrl;
    if (!moveUrl) {
        return;
    }

    var dragged = null;
    var draggableItems = tree.querySelectorAll("[data-draggable-item]");

    function clearInsertMarkers() {
        tree.querySelectorAll(".insert-before, .insert-after, .drag-over").forEach(function (el) {
            el.classList.remove("insert-before");
            el.classList.remove("insert-after");
            el.classList.remove("drag-over");
        });
    }

    function getContainerFolderId(el) {
        var zone = el.closest("[data-dropzone]");
        return zone ? zone.getAttribute("data-target-folder-id") || "" : "";
    }

    function isSameLevelReorderAllowed(item) {
        if (!dragged) {
            return false;
        }
        if (dragged.itemType !== item.getAttribute("data-item-type")) {
            return false;
        }
        var targetFolderId = getContainerFolderId(item);
        return String(targetFolderId) === String(dragged.sourceFolderId || "");
    }

    function nextSiblingSameType(item, itemType) {
        var sibling = item.nextElementSibling;
        while (sibling) {
            if (sibling.matches("[data-draggable-item]") && sibling.getAttribute("data-item-type") === itemType) {
                return sibling;
            }
            sibling = sibling.nextElementSibling;
        }
        return null;
    }

    draggableItems.forEach(function (item) {
        item.addEventListener("dragstart", function (evt) {
            var fromHandle = evt.target && evt.target.closest("[data-drag-handle]");
            if (!fromHandle) {
                evt.preventDefault();
                return;
            }

            dragged = {
                itemType: item.getAttribute("data-item-type"),
                itemId: item.getAttribute("data-item-id"),
                sourceFolderId: getContainerFolderId(item),
            };
            item.classList.add("dragging");
            if (evt.dataTransfer) {
                evt.dataTransfer.effectAllowed = "move";
                evt.dataTransfer.setData("text/plain", JSON.stringify(dragged));
            }
        });

        item.addEventListener("dragend", function () {
            item.classList.remove("dragging");
            dragged = null;
            clearInsertMarkers();
        });

        item.addEventListener("dragover", function (evt) {
            if (!isSameLevelReorderAllowed(item)) {
                return;
            }
            evt.preventDefault();
            evt.stopPropagation();
            clearInsertMarkers();
            var rect = item.getBoundingClientRect();
            var insertBefore = evt.clientY < rect.top + rect.height / 2;
            item.classList.add(insertBefore ? "insert-before" : "insert-after");
        });

        item.addEventListener("drop", function (evt) {
            if (!isSameLevelReorderAllowed(item)) {
                return;
            }
            evt.preventDefault();
            evt.stopPropagation();

            var isBefore = item.classList.contains("insert-before");
            var beforeItemId = null;
            if (isBefore) {
                beforeItemId = item.getAttribute("data-item-id");
            } else {
                var nextSameType = nextSiblingSameType(item, dragged.itemType);
                beforeItemId = nextSameType ? nextSameType.getAttribute("data-item-id") : null;
            }

            clearInsertMarkers();

            postJson(moveUrl, {
                item_type: dragged.itemType,
                item_id: dragged.itemId,
                target_folder_id: dragged.sourceFolderId,
                before_item_id: beforeItemId,
            })
                .then(function (resp) {
                    if (!resp.ok) {
                        throw new Error(resp.message || "Reorder failed");
                    }
                    window.location.reload();
                })
                .catch(function (err) {
                    window.alert(err.message || "Reorder failed");
                });
        });
    });

    document.querySelectorAll("[data-dropzone]").forEach(function (dropzone) {
        dropzone.addEventListener("dragover", function (evt) {
            evt.preventDefault();
            evt.stopPropagation();
            dropzone.classList.add("drag-over");
        });

        dropzone.addEventListener("dragleave", function (evt) {
            evt.stopPropagation();
            dropzone.classList.remove("drag-over");
        });

        dropzone.addEventListener("drop", function (evt) {
            evt.preventDefault();
            evt.stopPropagation();
            dropzone.classList.remove("drag-over");
            if (!dragged) {
                return;
            }

            var targetFolderId = dropzone.getAttribute("data-target-folder-id");
            postJson(moveUrl, {
                item_type: dragged.itemType,
                item_id: dragged.itemId,
                target_folder_id: targetFolderId,
                before_item_id: null,
            })
                .then(function (resp) {
                    if (!resp.ok) {
                        throw new Error(resp.message || "Move failed");
                    }
                    window.location.reload();
                })
                .catch(function (err) {
                    window.alert(err.message || "Move failed");
                });
        });
    });
}

function initExecutionWidget() {
    var widget = document.getElementById("executionWidget");
    if (!widget) {
        return;
    }

    var toggle = document.getElementById("executionWidgetToggle");
    var panel = document.getElementById("executionWidgetPanel");
    var body = document.getElementById("executionWidgetBody");
    var count = document.getElementById("executionActiveCount");
    var refreshState = document.getElementById("executionRefreshState");

    var csrf = getCsrfToken();
    var stateUrl = widget.dataset.liveStateUrl;
    var runBase = widget.dataset.runPauseUrlBase;
    var runStopBase = widget.dataset.runStopUrlBase;
    var jobBase = widget.dataset.jobPauseUrlBase;
    var jobStopBase = widget.dataset.jobStopUrlBase;

    var collapsed = localStorage.getItem("executionWidgetCollapsed") === "1";
    if (collapsed) {
        widget.classList.add("collapsed");
    }

    toggle.addEventListener("click", function () {
        widget.classList.toggle("collapsed");
        localStorage.setItem("executionWidgetCollapsed", widget.classList.contains("collapsed") ? "1" : "0");
    });

    body.addEventListener("click", function (evt) {
        var btn = evt.target.closest("button[data-action]");
        if (!btn) {
            return;
        }
        var action = btn.getAttribute("data-action");
        var runId = btn.getAttribute("data-run-id");
        var jobId = btn.getAttribute("data-job-id");
        var url = "";

        if (action === "run-pause") {
            url = runBase + runId + "/pause/";
        } else if (action === "run-stop") {
            url = runStopBase + runId + "/stop/";
        } else if (action === "job-pause") {
            url = jobBase + jobId + "/pause/";
        } else if (action === "job-stop") {
            url = jobStopBase + jobId + "/stop/";
        }

        if (!url) {
            return;
        }

        btn.disabled = true;
        postAction(url, csrf)
            .then(function () {
                fetchState();
            })
            .finally(function () {
                btn.disabled = false;
            });
    });

    function fetchState() {
        if (!stateUrl) {
            return;
        }
        fetch(stateUrl, {
            headers: { "X-Requested-With": "XMLHttpRequest" },
            credentials: "same-origin",
        })
            .then(function (resp) {
                if (!resp.ok) {
                    throw new Error("Failed to fetch state");
                }
                return resp.json();
            })
            .then(function (data) {
                renderRuns(data && data.runs ? data.runs : []);
                refreshState.textContent = "Auto refresh";
            })
            .catch(function () {
                refreshState.textContent = "Refresh failed";
            });
    }

    function renderRuns(runs) {
        var activeRuns = runs.filter(function (r) {
            return r.status === "RUNNING";
        });
        var hasActiveExecution = activeRuns.length > 0;

        widget.classList.toggle("no-execution-hidden", !hasActiveExecution);

        count.textContent = String(activeRuns.length);

        if (!hasActiveExecution) {
            body.innerHTML = '<div class="text-muted small">No active runs.</div>';
            return;
        }

        var html = "";
        activeRuns.forEach(function (run) {
            var jobsHtml = "";
            var runningCount = 0;
            
            run.jobs.forEach(function (job, idx) {
                var jobFinished = ["PASS", "FAIL", "ERROR", "STOPPED"].indexOf(job.status) >= 0;
                if (job.status === "RUNNING") runningCount++;
                
                var actionBtns = '';
                if (!jobFinished) {
                    actionBtns = '<div class="exec-job-actions">' +
                        '<button type="button" class="exec-job-btn exec-pause-btn" data-action="job-pause" data-job-id="' +
                        job.id + '" title="Pause/Resume"><i class="bi bi-pause-fill"></i></button>' +
                        '<button type="button" class="exec-job-btn exec-stop-btn" data-action="job-stop" data-job-id="' +
                        job.id + '" title="Stop"><i class="bi bi-stop-fill"></i></button>' +
                        '</div>';
                }
                
                jobsHtml +=
                    '<div class="exec-job-item">' +
                    '<span class="exec-job-index">' + (idx + 1) + '</span>' +
                    '<div class="exec-job-info">' +
                    '<div class="exec-job-name">' + escapeHtml(job.name) + '</div>' +
                    '</div>' +
                    '<div class="exec-job-status">' +
                    statusPill(job.status) +
                    "</div>" +
                    actionBtns +
                    "</div>";
            });

            var done = run.completed + "/" + run.total;
            var runFinished = run.status === "COMPLETED";
            
            html +=
                '<div class="exec-run-card">' +
                '<div class="exec-run-header">' +
                '<div class="exec-header-title">' +
                '<strong>Executing Tests</strong> <span class="exec-progress">' + run.completed + '/' + run.total + '</span>' +
                "</div>" +
                '<div class="exec-header-controls">' +
                '<button type="button" class="exec-header-btn exec-pause-all-btn" data-action="run-pause" data-run-id="' +
                run.id + '" ' +
                (runFinished ? "disabled" : "") +
                "><i class="bi bi-pause-fill"></i> Pause All</button>" +
                '<button type="button" class="exec-header-btn exec-stop-all-btn" data-action="run-stop" data-run-id="' +
                run.id + '" ' +
                (runFinished ? "disabled" : "") +
                "><i class="bi bi-stop-fill"></i> Stop All</button>" +
                "</div>" +
                "</div>" +
                '<div class="exec-run-jobs">' +
                jobsHtml +
                "</div>" +
                "</div>";
        });

        body.innerHTML = html;
    }

    fetchState();
    setInterval(fetchState, 2500);

    if (!panel) {
        widget.classList.add("collapsed");
    }
}

function postAction(url, csrfToken) {
    return fetch(url, {
        method: "POST",
        headers: {
            "X-CSRFToken": csrfToken,
            "X-Requested-With": "XMLHttpRequest",
        },
        credentials: "same-origin",
    }).then(function (resp) {
        if (!resp.ok) {
            throw new Error("Action failed");
        }
        return resp.json();
    });
}

function postJson(url, payload) {
    return fetch(url, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCsrfToken(),
            "X-Requested-With": "XMLHttpRequest",
        },
        credentials: "same-origin",
        body: JSON.stringify(payload || {}),
    }).then(function (resp) {
        return resp.json().then(function (data) {
            return {
                ok: resp.ok && data.ok,
                message: data.message || "",
            };
        });
    });
}

function getCsrfToken() {
    var m = document.cookie.match(/csrftoken=([^;]+)/);
    return m ? m[1] : "";
}

function statusPill(status) {
    var cls = "status-error";
    if (status === "PASS") {
        cls = "status-pass";
    } else if (status === "FAIL" || status === "STOPPED") {
        cls = "status-fail";
    }
    return '<span class="status-pill ' + cls + '">' + escapeHtml(status) + "</span>";
}

function escapeHtml(value) {
    return String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\"/g, "&quot;")
        .replace(/'/g, "&#39;");
}
