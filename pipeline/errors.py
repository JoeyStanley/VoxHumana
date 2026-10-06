class UserFacingError(RuntimeError):
    """A failure whose message is written for the person who submitted the job.

    Raise this for problems a user can understand and act on: no speech in
    the audio, a TextGrid with the wrong tiers, a recording too long to
    finish in time. The web app shows its message verbatim. Every other
    exception is treated as internal: the user sees a generic message and
    the details go only to the server-side error log.

    So keep server file paths, tool output (stdout/stderr), and version
    details out of these messages.
    """
