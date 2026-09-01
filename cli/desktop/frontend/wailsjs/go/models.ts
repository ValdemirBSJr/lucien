export namespace main {
	
	export class AppInfo {
	    productName: string;
	    version: string;
	    copyright: string;
	
	    static createFrom(source: any = {}) {
	        return new AppInfo(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.productName = source["productName"];
	        this.version = source["version"];
	        this.copyright = source["copyright"];
	    }
	}
	export class ConnectionSettings {
	    apiHost: string;
	    caFile: string;
	
	    static createFrom(source: any = {}) {
	        return new ConnectionSettings(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.apiHost = source["apiHost"];
	        this.caFile = source["caFile"];
	    }
	}
	export class Identity {
	    id: string;
	    username: string;
	    roleLevel: string;
	    domainFunction: string;
	    extraDomains: string[];
	
	    static createFrom(source: any = {}) {
	        return new Identity(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.username = source["username"];
	        this.roleLevel = source["roleLevel"];
	        this.domainFunction = source["domainFunction"];
	        this.extraDomains = source["extraDomains"];
	    }
	}
	export class PublishedRunbookSummary {
	    id: string;
	    name: string;
	
	    static createFrom(source: any = {}) {
	        return new PublishedRunbookSummary(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.name = source["name"];
	    }
	}
	export class RunbookDetail {
	    id: string;
	    name: string;
	    status: string;
	    description: string;
	    commands: string[];
	    commandOutputs: string[];
	    objective: string;
	    architecturePrerequisites: string[];
	    commandImpacts: string[];
	    rollbackCommands: string[];
	
	    static createFrom(source: any = {}) {
	        return new RunbookDetail(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.name = source["name"];
	        this.status = source["status"];
	        this.description = source["description"];
	        this.commands = source["commands"];
	        this.commandOutputs = source["commandOutputs"];
	        this.objective = source["objective"];
	        this.architecturePrerequisites = source["architecturePrerequisites"];
	        this.commandImpacts = source["commandImpacts"];
	        this.rollbackCommands = source["rollbackCommands"];
	    }
	}
	export class RunbookRow {
	    id: string;
	    name: string;
	    status: string;
	    description: string;
	    createdAt: string;
	    processingError: string;
	
	    static createFrom(source: any = {}) {
	        return new RunbookRow(source);
	    }
	
	    constructor(source: any = {}) {
	        if ('string' === typeof source) source = JSON.parse(source);
	        this.id = source["id"];
	        this.name = source["name"];
	        this.status = source["status"];
	        this.description = source["description"];
	        this.createdAt = source["createdAt"];
	        this.processingError = source["processingError"];
	    }
	}

}

