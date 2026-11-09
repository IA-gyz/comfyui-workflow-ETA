from .workflow_eta_tracker import install_workflow_eta

_workflow_eta_tracker = install_workflow_eta()


class WorkflowETA:
    """Frontend display; registered in Python for reliable node discovery."""
    CATEGORY = "Workflow"
    RETURN_TYPES = ()
    FUNCTION = "display_workflow_eta"
    DESCRIPTION = "Live workflow ETA. No connections required."

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {}}

    def display_workflow_eta(self):
        return ()


NODE_CLASS_MAPPINGS = {"WorkflowETA": WorkflowETA}
NODE_DISPLAY_NAME_MAPPINGS = {"WorkflowETA": "Workflow ETA"}
WEB_DIRECTORY = "./js"
