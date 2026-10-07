document.addEventListener("DOMContentLoaded", () => {

    // =====================================================
    // CONFIGURATION
    // =====================================================

    const MAX_FILE_SIZE = 100 * 1024 * 1024; // 100 MB
    const MAX_FILE_SIZE_MB = 100;


    // =====================================================
    // ELEMENTS
    // =====================================================

    const fileInput = document.getElementById("fileInput");
    const dropZone = document.getElementById("dropZone");
    const browseButton = document.getElementById("browseButton");
    const uploadForm = document.getElementById("uploadForm");

    const selectedFile = document.getElementById("selectedFile");
    const selectedFileName = document.getElementById("selectedFileName");
    const selectedFileSize = document.getElementById("selectedFileSize");

    const uploadButton = document.getElementById("uploadButton");
    const progressWrapper = document.getElementById("progressWrapper");
    const progressBar = document.getElementById("progressBar");
    const progressPercent = document.getElementById("progressPercent");

    const searchInput = document.getElementById("searchInput");
    const noSearchResults = document.getElementById("noSearchResults");

    const toast = document.getElementById("toast");
    const toastMessage = document.getElementById("toastMessage");
    const toastIcon = document.getElementById("toastIcon");


    // =====================================================
    // TOAST
    // =====================================================

    function showToast(message, success = true) {

        if (!toast) {
            return;
        }

        toastMessage.textContent = message;

        toastIcon.textContent = success ? "✓" : "!";

        toast.classList.add("show");

        setTimeout(() => {
            toast.classList.remove("show");
        }, 3200);
    }


    // =====================================================
    // FILE SIZE
    // =====================================================

    function formatFileSize(bytes) {

        if (bytes < 1024) {
            return `${bytes} B`;
        }

        if (bytes < 1024 * 1024) {
            return `${(bytes / 1024).toFixed(1)} KB`;
        }

        if (bytes < 1024 * 1024 * 1024) {
            return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
        }

        return `${(
            bytes /
            1024 /
            1024 /
            1024
        ).toFixed(1)} GB`;
    }


    // =====================================================
    // FILE SELECTION
    // =====================================================

    function handleFile(file) {

        if (!file) {
            return;
        }


        // Check 100 MB limit

        if (file.size > MAX_FILE_SIZE) {

            showToast(
                `Maximum file size is ${MAX_FILE_SIZE_MB} MB.`,
                false
            );

            if (fileInput) {
                fileInput.value = "";
            }

            if (selectedFile) {
                selectedFile.hidden = true;
            }

            return;
        }


        // Show selected file

        if (selectedFile) {
            selectedFile.hidden = false;
        }


        if (selectedFileName) {
            selectedFileName.textContent = file.name;
        }


        if (selectedFileSize) {
            selectedFileSize.textContent =
                formatFileSize(file.size);
        }


        if (dropZone) {
            dropZone.classList.add("has-file");
        }
    }


    // =====================================================
    // BROWSE BUTTON
    // =====================================================

    if (browseButton && fileInput) {

        browseButton.addEventListener(
            "click",
            event => {

                event.stopPropagation();

                fileInput.click();
            }
        );

    }


    // =====================================================
    // FILE INPUT
    // =====================================================

    if (fileInput) {

        fileInput.addEventListener(
            "change",
            () => {

                const file =
                    fileInput.files[0];

                handleFile(file);
            }
        );

    }


    // =====================================================
    // DRAG AND DROP
    // =====================================================

    if (dropZone && fileInput) {

        dropZone.addEventListener(
            "dragover",
            event => {

                event.preventDefault();

                dropZone.classList.add("dragging");
            }
        );


        dropZone.addEventListener(
            "dragleave",
            () => {

                dropZone.classList.remove("dragging");
            }
        );


        dropZone.addEventListener(
            "drop",
            event => {

                event.preventDefault();

                dropZone.classList.remove("dragging");

                const file =
                    event.dataTransfer.files[0];

                if (!file) {
                    return;
                }


                // Put dropped file into file input

                try {

                    const dataTransfer =
                        new DataTransfer();

                    dataTransfer.items.add(file);

                    fileInput.files =
                        dataTransfer.files;

                } catch (error) {

                    console.warn(
                        "Could not assign dropped file:",
                        error
                    );
                }


                handleFile(file);
            }
        );

    }


    // =====================================================
    // UPLOAD
    // =====================================================

    if (uploadForm) {

        uploadForm.addEventListener(
            "submit",
            event => {

                event.preventDefault();


                // Make sure file exists

                if (
                    !fileInput ||
                    !fileInput.files ||
                    !fileInput.files.length
                ) {

                    showToast(
                        "Please choose a file first.",
                        false
                    );

                    return;
                }


                const file =
                    fileInput.files[0];


                // Check size again

                if (file.size > MAX_FILE_SIZE) {

                    showToast(
                        `Maximum file size is ${MAX_FILE_SIZE_MB} MB.`,
                        false
                    );

                    return;
                }


                // Create form data

                const formData =
                    new FormData(uploadForm);


                // Disable upload button

                uploadButton.disabled = true;

                uploadButton.textContent =
                    "Uploading...";


                // Show progress

                progressWrapper.hidden = false;

                progressBar.style.width = "0%";

                progressPercent.textContent = "0%";


                // Create request

                const xhr =
                    new XMLHttpRequest();


                xhr.open(
                    "POST",
                    "/upload",
                    true
                );


                // =================================================
                // UPLOAD PROGRESS
                // =================================================

                xhr.upload.addEventListener(
                    "progress",
                    event => {

                        if (!event.lengthComputable) {
                            return;
                        }


                        const percent =
                            Math.round(
                                (event.loaded /
                                    event.total) *
                                100
                            );


                        progressBar.style.width =
                            `${percent}%`;

                        progressPercent.textContent =
                            `${percent}%`;
                    }
                );


                // =================================================
                // UPLOAD COMPLETE
                // =================================================

                xhr.onload = () => {

                    uploadButton.disabled = false;

                    uploadButton.textContent =
                        "Upload to Azure";


                    let data = null;


                    try {

                        data =
                            JSON.parse(
                                xhr.responseText
                            );

                    } catch (error) {

                        console.error(
                            "Server response:",
                            xhr.responseText
                        );
                    }


                    // Successful upload

                    if (
                        xhr.status >= 200 &&
                        xhr.status < 300 &&
                        data &&
                        data.success
                    ) {

                        progressBar.style.width =
                            "100%";

                        progressPercent.textContent =
                            "100%";


                        showToast(
                            "File uploaded successfully."
                        );


                        // Reload files

                        setTimeout(() => {

                            window.location.reload();

                        }, 800);

                        return;
                    }


                    // Server returned an error

                    let message =
                        "Upload failed.";


                    if (data && data.message) {
                        message = data.message;
                    }


                    if (xhr.status === 401) {

                        message =
                            "Please login before uploading.";
                    }


                    if (xhr.status === 403) {

                        message =
                            "You are not allowed to upload files.";
                    }


                    if (xhr.status === 413) {

                        message =
                            "File is too large. Maximum size is 100 MB.";
                    }


                    showToast(
                        message,
                        false
                    );


                    progressWrapper.hidden = true;
                };


                // =================================================
                // NETWORK ERROR
                // =================================================

                xhr.onerror = () => {

                    uploadButton.disabled = false;

                    uploadButton.textContent =
                        "Upload to Azure";


                    progressWrapper.hidden = true;


                    showToast(
                        "Network error. Check the Flask server.",
                        false
                    );
                };


                // =================================================
                // REQUEST ABORTED
                // =================================================

                xhr.onabort = () => {

                    uploadButton.disabled = false;

                    uploadButton.textContent =
                        "Upload to Azure";


                    progressWrapper.hidden = true;


                    showToast(
                        "Upload cancelled.",
                        false
                    );
                };


                // Send to Flask

                xhr.send(formData);

            }
        );

    }


    // =====================================================
    // SEARCH FILES
    // =====================================================

    if (searchInput) {

        searchInput.addEventListener(
            "input",
            () => {

                const query =
                    searchInput.value
                        .trim()
                        .toLowerCase();


                const cards =
                    document.querySelectorAll(
                        ".file-card"
                    );


                let visibleCount = 0;


                cards.forEach(card => {

                    const name =
                        card.dataset.name || "";


                    const visible =
                        name.includes(query);


                    card.style.display =
                        visible ? "" : "none";


                    if (visible) {
                        visibleCount++;
                    }
                });


                if (noSearchResults) {

                    noSearchResults.hidden =
                        visibleCount !== 0;
                }
            }
        );

    }


    // =====================================================
    // SHARE
    // =====================================================

    const shareModal =
        document.getElementById("shareModal");

    const shareLink =
        document.getElementById("shareLink");

    const copyShareLink =
        document.getElementById("copyShareLink");


    document.querySelectorAll(
        ".file-share"
    ).forEach(button => {

        button.addEventListener(
            "click",
            async () => {

                const fileId =
                    button.dataset.id;


                try {

                    const response =
                        await fetch(
                            `/share/${fileId}`
                        );


                    const data =
                        await response.json();


                    if (!response.ok || !data.success) {

                        showToast(
                            data.message ||
                            "Unable to create share link.",
                            false
                        );

                        return;
                    }


                    shareLink.value =
                        data.url;


                    shareModal.hidden =
                        false;

                    document.body.classList.add(
                        "modal-open"
                    );

                } catch (error) {

                    console.error(error);

                    showToast(
                        "Could not create share link.",
                        false
                    );
                }

            }
        );

    });


    // =====================================================
    // COPY SHARE LINK
    // =====================================================

    if (copyShareLink) {

        copyShareLink.addEventListener(
            "click",
            async () => {

                try {

                    await navigator.clipboard.writeText(
                        shareLink.value
                    );


                    copyShareLink.textContent =
                        "Copied!";


                    setTimeout(() => {

                        copyShareLink.textContent =
                            "Copy";

                    }, 1800);

                } catch (error) {

                    shareLink.select();

                    document.execCommand("copy");

                    copyShareLink.textContent =
                        "Copied!";

                    setTimeout(() => {

                        copyShareLink.textContent =
                            "Copy";

                    }, 1800);
                }
            }
        );

    }


    // =====================================================
    // DELETE
    // =====================================================

    const deleteModal =
        document.getElementById("deleteModal");

    const deleteFileName =
        document.getElementById("deleteFileName");

    const confirmDelete =
        document.getElementById("confirmDelete");


    let pendingDeleteId = null;


    document.querySelectorAll(
        ".file-delete"
    ).forEach(button => {

        button.addEventListener(
            "click",
            () => {

                pendingDeleteId =
                    button.dataset.id;


                deleteFileName.textContent =
                    button.dataset.name;


                deleteModal.hidden =
                    false;


                document.body.classList.add(
                    "modal-open"
                );
            }
        );

    });


    // =====================================================
    // CONFIRM DELETE
    // =====================================================

    if (confirmDelete) {

        confirmDelete.addEventListener(
            "click",
            async () => {

                if (!pendingDeleteId) {
                    return;
                }


                confirmDelete.disabled = true;

                confirmDelete.textContent =
                    "Deleting...";


                try {

                    const response =
                        await fetch(
                            `/delete/${pendingDeleteId}`,
                            {
                                method: "POST"
                            }
                        );


                    const data =
                        await response.json();


                    if (!response.ok || !data.success) {

                        showToast(
                            data.message ||
                            "Delete failed.",
                            false
                        );

                        return;
                    }


                    showToast(
                        "File deleted successfully."
                    );


                    setTimeout(() => {

                        window.location.reload();

                    }, 600);

                } catch (error) {

                    console.error(error);

                    showToast(
                        "Could not delete file.",
                        false
                    );

                } finally {

                    confirmDelete.disabled = false;

                    confirmDelete.textContent =
                        "Delete File";
                }

            }
        );

    }


    // =====================================================
    // SETTINGS
    // =====================================================

    const settingsModal =
        document.getElementById("settingsModal");

    const openSettings =
        document.getElementById("openSettings");

    const profileSettings =
        document.getElementById("profileSettings");


    function openSettingsModal() {

        if (!settingsModal) {
            return;
        }


        settingsModal.hidden = false;

        document.body.classList.add(
            "modal-open"
        );
    }


    if (openSettings) {

        openSettings.addEventListener(
            "click",
            openSettingsModal
        );
    }


    if (profileSettings) {

        profileSettings.addEventListener(
            "click",
            openSettingsModal
        );
    }


    // =====================================================
    // PROFILE DROPDOWN
    // =====================================================

    const profileButton =
        document.getElementById("profileButton");

    const profileDropdown =
        document.getElementById("profileDropdown");


    if (profileButton) {

        profileButton.addEventListener(
            "click",
            event => {

                event.stopPropagation();

                if (profileDropdown) {

                    profileDropdown.classList.toggle(
                        "show"
                    );
                }
            }
        );
    }


    document.addEventListener(
        "click",
        event => {

            if (
                profileDropdown &&
                profileButton &&
                !profileDropdown.contains(event.target) &&
                !profileButton.contains(event.target)
            ) {

                profileDropdown.classList.remove(
                    "show"
                );
            }
        }
    );


    // =====================================================
    // CLOSE MODALS
    // =====================================================

    document.querySelectorAll(
        "[data-close]"
    ).forEach(button => {

        button.addEventListener(
            "click",
            () => {

                const modalId =
                    button.dataset.close;


                const modal =
                    document.getElementById(
                        modalId
                    );


                if (modal) {

                    modal.hidden = true;

                    document.body.classList.remove(
                        "modal-open"
                    );
                }
            }
        );

    });


    // =====================================================
    // CLOSE MODAL BY CLICKING OUTSIDE
    // =====================================================

    document.querySelectorAll(
        ".modal-overlay"
    ).forEach(overlay => {

        overlay.addEventListener(
            "click",
            event => {

                if (
                    event.target === overlay
                ) {

                    overlay.hidden = true;

                    document.body.classList.remove(
                        "modal-open"
                    );
                }
            }
        );

    });


    // =====================================================
    // ESCAPE KEY
    // =====================================================

    document.addEventListener(
        "keydown",
        event => {

            if (event.key !== "Escape") {
                return;
            }


            document.querySelectorAll(
                ".modal-overlay"
            ).forEach(modal => {

                modal.hidden = true;
            });


            document.body.classList.remove(
                "modal-open"
            );
        }
    );


    // =====================================================
    // THEME
    // =====================================================

    const themeSwitch =
        document.getElementById("themeSwitch");


    const savedTheme =
        localStorage.getItem(
            "cloud-workspace-theme"
        );


    if (savedTheme === "dark") {

        document.body.classList.add(
            "dark-theme"
        );
    }


    if (themeSwitch) {

        themeSwitch.addEventListener(
            "click",
            () => {

                document.body.classList.toggle(
                    "dark-theme"
                );


                const dark =
                    document.body.classList.contains(
                        "dark-theme"
                    );


                localStorage.setItem(
                    "cloud-workspace-theme",
                    dark ? "dark" : "light"
                );
            }
        );

    }

});